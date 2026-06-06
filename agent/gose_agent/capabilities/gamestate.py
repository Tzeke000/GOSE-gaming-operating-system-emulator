"""Game-state capability — "Mineflayer for retro games".

Gives the AI structured game state (positions, scores, health, board state)
read directly from the emulator's memory, instead of screenshots. Works by
talking to RetroArch's Network Command Interface (UDP 55355) with
READ_CORE_MEMORY / WRITE_CORE_MEMORY, then decoding raw bytes through a
per-game "profile" that maps memory addresses to named, typed fields.

Honest caveats (see docs/08-game-state-interface.md):
- Requires `network_cmd_enable=true` in retroarch.cfg.
- Not all cores expose a system memory map to the NCI. Confirmed-working include
  Mupen64Plus-Next (N64 / Mario 64) and Mesen (NES). Cores without a map return
  "no memory map defined" — the profile then can't be read and we say so clearly.
- Addresses are game+core specific and must be sourced from a RAM map and
  verified on hardware (RetroAchievements Memory Inspector is the easiest way).
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

from ..protocol import AgentError, ERR_ARGS, ERR_BACKEND

log = logging.getLogger("gose.agent.gamestate")

# Friendly aliases -> stable-retro/numpy-style descriptors. We accept BOTH our
# readable names and stable-retro's own type strings (e.g. ">u4", "<i2", "|u1"),
# so RAM maps imported from stable-retro/gym-retro work unchanged.
_ALIASES = {
    "u8": "|u1", "s8": "|i1",
    "u16": "u2", "s16": "i2",
    "u32": "u4", "s32": "i4",
    "float32": "f4", "float64": "f8",
}
# kind+width -> struct format char.
_STRUCT = {
    ("u", 1): "B", ("i", 1): "b",
    ("u", 2): "H", ("i", 2): "h",
    ("u", 4): "I", ("i", 4): "i",
    ("u", 8): "Q", ("i", 8): "q",
    ("f", 4): "f", ("f", 8): "d",
}
# BCD kinds (stable-retro descriptors): 'd' = packed BCD, two digits per byte
# (e.g. ">d2" = a 4-digit score in 2 bytes); 'n' = one digit in each byte's LOW
# nybble (e.g. ">n6" = SMB's 6-byte score). struct has no format char for these,
# so resolve_type returns a pseudo-format that decode_field branches on.
_BCD_FMT = {"d": "bcd_d", "n": "bcd_n"}
_BCD_MAX_BYTES = 8
_DESC_RE = re.compile(r"^([<>|=]?)([uifdn])(\d+)$")
_BCD_ALIAS_RE = re.compile(r"^bcd_([dn])(\d+)$")


def resolve_type(type_str: str, default_endian: str) -> Tuple[str, str, int]:
    """Map a type string to (struct_endian_char, fmt, byte_size).

    Accepts our aliases ("u16", "bcd_n6", "bcd_d2") and stable-retro descriptors
    ("<u2", ">i4", "|u1", ">n6", ">d2"). For BCD kinds, fmt is the pseudo-format
    "bcd_d" / "bcd_n" (see _BCD_FMT) rather than a struct char — decode_field
    handles those without struct. The "bcd_*" aliases carry no endianness, so
    the profile default applies; ">" means first byte is most significant.
    """
    desc = _ALIASES.get(type_str, type_str)
    bcd = _BCD_ALIAS_RE.match(desc)
    if bcd:
        desc = bcd.group(1) + bcd.group(2)  # "bcd_n6" -> "n6" (default endian)
    m = _DESC_RE.match(desc)
    if not m:
        raise AgentError(ERR_ARGS, f"unsupported type '{type_str}'")
    endian_sym, kind, width = m.group(1), m.group(2), int(m.group(3))
    if kind in _BCD_FMT:
        if not 1 <= width <= _BCD_MAX_BYTES:
            raise AgentError(ERR_ARGS, f"unsupported type '{type_str}'")
        fmt = _BCD_FMT[kind]
    elif (kind, width) in _STRUCT:
        fmt = _STRUCT[(kind, width)]
    else:
        raise AgentError(ERR_ARGS, f"unsupported type '{type_str}'")
    if endian_sym in (">",):
        endc = ">"
    elif endian_sym in ("<",):
        endc = "<"
    elif endian_sym in ("|", "") and width == 1:
        endc = "<"  # endianness irrelevant for 1-byte
    else:
        endc = "<" if default_endian == "little" else ">"
    return endc, fmt, width


def decode_bcd(raw: bytes, fmt: str, endc: str) -> int:
    """Decode BCD bytes. "bcd_d" = packed (high nybble tens, low nybble ones);
    "bcd_n" = one digit in each byte's low nybble (high nybble ignored).
    endc ">" = first byte is the most significant digit(s)."""
    data = raw if endc == ">" else bytes(reversed(raw))
    value = 0
    for b in data:
        if fmt == "bcd_d":
            value = value * 100 + ((b >> 4) & 0xF) * 10 + (b & 0xF)
        else:
            value = value * 10 + (b & 0xF)
    return value


class RetroArchClient:
    """Minimal UDP client for the RetroArch Network Command Interface."""

    def __init__(self, host: str = "127.0.0.1", port: int = 55355, timeout: float = 1.0):
        self.host, self.port, self.timeout = host, port, timeout

    def _cmd(self, text: str) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            sock.sendto(text.encode("ascii"), (self.host, self.port))
            data, _ = sock.recvfrom(8192)
            return data.decode("ascii", "replace").strip()
        except socket.timeout as e:
            raise AgentError(ERR_BACKEND, f"RetroArch did not respond at "
                                          f"{self.host}:{self.port} (is it running with "
                                          f"network_cmd_enable=true?)") from e
        except OSError as e:
            raise AgentError(ERR_BACKEND, f"RetroArch socket error: {e}") from e
        finally:
            sock.close()

    def read_memory(self, address: int, count: int, method: str = "core_memory") -> bytes:
        """Read bytes via the system memory map (core_memory) or achievement/RAM
        offsets (core_ram). stable-retro maps use RAM offsets -> core_ram."""
        verb = "READ_CORE_RAM" if method == "core_ram" else "READ_CORE_MEMORY"
        resp = self._cmd(f"{verb} {address:x} {count}")
        parts = resp.split()
        if "-1" in parts or " -1 " in resp:
            raise AgentError(ERR_BACKEND, f"read failed: {resp}")
        if len(parts) < 2 or parts[0] != verb:
            raise AgentError(ERR_BACKEND, f"unexpected response: {resp}")
        try:
            return bytes(int(b, 16) for b in parts[2:])
        except ValueError as e:
            raise AgentError(ERR_BACKEND, f"bad hex in response: {resp}") from e

    # Back-compat alias.
    def read_core_memory(self, address: int, count: int) -> bytes:
        return self.read_memory(address, count, "core_memory")

    def write_memory(self, address: int, data: bytes, method: str = "core_memory") -> int:
        verb = "WRITE_CORE_RAM" if method == "core_ram" else "WRITE_CORE_MEMORY"
        hexbytes = " ".join(f"{b:02x}" for b in data)
        resp = self._cmd(f"{verb} {address:x} {hexbytes}")
        parts = resp.split()
        if "-1" in parts:
            raise AgentError(ERR_BACKEND, f"write failed: {resp}")
        # "WRITE_CORE_MEMORY <addr> <nbytes>"
        try:
            return int(parts[2])
        except (IndexError, ValueError):
            return len(data)

    # Back-compat alias.
    def write_core_memory(self, address: int, data: bytes) -> int:
        return self.write_memory(address, data, "core_memory")

    def status(self) -> Dict[str, Any]:
        resp = self._cmd("GET_STATUS")
        # "GET_STATUS PLAYING <core>,<game>,<crc>" / "GET_STATUS CONTENTLESS" etc.
        body = resp[len("GET_STATUS"):].strip() if resp.startswith("GET_STATUS") else resp
        bits = body.split(" ", 1)
        state = bits[0] if bits else "UNKNOWN"
        detail = bits[1] if len(bits) > 1 else ""
        core = game = crc = None
        if "," in detail:
            fields = detail.split(",")
            core = fields[0] or None
            game = fields[1] if len(fields) > 1 else None
            crc = fields[2] if len(fields) > 2 else None
        return {"state": state, "core": core, "game": game, "crc": crc, "raw": resp}


class GameProfile:
    """Maps memory addresses to named, typed fields for one game."""

    def __init__(self, data: Dict[str, Any], source: Optional[str] = None):
        self.source = source
        self.name = data["name"]
        self.system = data.get("system", "")
        self.core = data.get("core", "")
        self.match = data.get("match", {})            # {"game_substr": "...", "crc": "..."}
        self.endian = data.get("endian", "little")    # "little" | "big"
        self.read_method = data.get("read_method", "core_memory")
        self.notes = data.get("notes", "")
        self.fields: List[Dict[str, Any]] = data["fields"]
        self._validate()

    def _validate(self):
        for f in self.fields:
            if "name" not in f or "address" not in f or "type" not in f:
                raise AgentError(ERR_ARGS, f"profile '{self.name}': field needs name/address/type")
            resolve_type(f["type"], self.endian)  # raises on bad type

    @staticmethod
    def addr(v) -> int:
        # 0x-prefixed string -> hex; bare string -> decimal (stable-retro style);
        # int -> as-is.
        if isinstance(v, str):
            s = v.strip().lower()
            return int(s, 16) if s.startswith("0x") else int(s)
        return int(v)

    def field_size(self, f: Dict[str, Any]) -> int:
        return resolve_type(f["type"], f.get("endian", self.endian))[2]

    def decode_field(self, f: Dict[str, Any], raw: bytes) -> Any:
        endc, fmt, size = resolve_type(f["type"], f.get("endian", self.endian))
        if fmt in ("bcd_d", "bcd_n"):
            value = decode_bcd(raw[:size], fmt, endc)
        else:
            value = struct.unpack(endc + fmt, raw[:size])[0]
        if "scale" in f:
            value = value * f["scale"]
        if f.get("bool"):
            value = bool(value)
        return value

    def to_summary(self) -> Dict[str, Any]:
        return {"name": self.name, "system": self.system, "core": self.core,
                "fields": [f["name"] for f in self.fields], "notes": self.notes,
                "match": self.match}


def load_profiles(profiles_dir: str) -> Tuple[Dict[str, GameProfile], Dict[str, str]]:
    """Load all profile JSONs. Returns (profiles, skipped) — skipped maps
    filename -> reason for every file that failed to parse/validate, so bad
    profiles are visible instead of silently vanishing."""
    out: Dict[str, GameProfile] = {}
    skipped: Dict[str, str] = {}
    if not profiles_dir or not os.path.isdir(profiles_dir):
        return out, skipped
    for path in glob.glob(os.path.join(profiles_dir, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            key = os.path.splitext(os.path.basename(path))[0]
            out[key] = GameProfile(data, source=path)
        except (json.JSONDecodeError, KeyError, AgentError) as e:
            # Skip malformed profiles rather than crashing the agent — but say so.
            reason = e.message if isinstance(e, AgentError) else f"{type(e).__name__}: {e}"
            skipped[os.path.basename(path)] = reason
            log.warning("skipping malformed profile %s: %s", path, reason)
    return out, skipped


class GameStateCapability:
    def __init__(self, profiles_dir: str, ra_host: str = "127.0.0.1",
                 ra_port: int = 55355):
        self.profiles_dir = profiles_dir
        self.profiles, self.profiles_skipped = load_profiles(profiles_dir)
        self.ra = RetroArchClient(ra_host, ra_port)
        self.active: Optional[str] = None
        # "real" means we'll genuinely talk UDP; reachability is checked per-call.
        self.backend = "retroarch"

    # ---- ops ----
    def list_profiles(self) -> Dict[str, Any]:
        return {"profiles": {k: p.to_summary() for k, p in self.profiles.items()},
                "skipped": self.profiles_skipped,  # filename -> reason (bad JSONs)
                "profiles_dir": self.profiles_dir, "active": self.active}

    def status(self) -> Dict[str, Any]:
        return self.ra.status()

    def attach(self, profile: Optional[str] = None) -> Dict[str, Any]:
        if profile:
            if profile not in self.profiles:
                raise AgentError(ERR_ARGS, f"no such profile '{profile}'")
            self.active = profile
            return {"attached": profile, "detected": False}
        # Auto-detect via RetroArch status + profile match rules.
        st = self.ra.status()
        game = (st.get("game") or "").lower()
        crc = (st.get("crc") or "").lower()
        for key, p in self.profiles.items():
            m = p.match or {}
            if m.get("crc") and crc and m["crc"].lower() == crc:
                self.active = key
                return {"attached": key, "detected": True, "by": "crc", "status": st}
            sub = (m.get("game_substr") or "").lower()
            if sub and sub in game:
                self.active = key
                return {"attached": key, "detected": True, "by": "game_substr", "status": st}
        raise AgentError(ERR_BACKEND, f"no profile matched running game (status={st})")

    def read(self, profile: Optional[str] = None) -> Dict[str, Any]:
        key = profile or self.active
        if not key:
            raise AgentError(ERR_ARGS, "no active profile; call state.attach first")
        if key not in self.profiles:
            raise AgentError(ERR_ARGS, f"no such profile '{key}'")
        p = self.profiles[key]
        fields: Dict[str, Any] = {}
        for f in p.fields:
            size = p.field_size(f)
            # Per-field read_method override: imported profiles fall back to
            # core_memory for fields outside the console's READ_CORE_RAM window.
            method = f.get("read_method", p.read_method)
            raw = self.ra.read_memory(GameProfile.addr(f["address"]), size, method)
            fields[f["name"]] = p.decode_field(f, raw)
        return {"profile": key, "fields": fields, "ts": time.time()}

    def read_raw(self, address, count: int, method: str = "core_memory") -> Dict[str, Any]:
        addr = GameProfile.addr(address)
        raw = self.ra.read_memory(addr, int(count), method)
        return {"address": f"{addr:x}", "count": len(raw),
                "bytes": list(raw), "hex": raw.hex()}

    def write_raw(self, address, data, method: str = "core_memory") -> Dict[str, Any]:
        addr = GameProfile.addr(address)
        if isinstance(data, str):
            data = bytes.fromhex(data)
        else:
            data = bytes(int(b) & 0xFF for b in data)
        n = self.ra.write_memory(addr, data, method)
        return {"address": f"{addr:x}", "written": n}
