#!/usr/bin/env python3
"""Import RAM maps from stable-retro / gym-retro into GOSE game profiles.

stable-retro (Farama Foundation, the maintained gym-retro fork) ships community
RAM maps for hundreds of games as `data.json`:

    { "info": { "score": { "address": 128, "type": ">u4" },
                "lives": { "address": 1234, "type": "|u1" } } }

This converts one game's `data.json` into a GOSE profile (see
../gose_agent/profiles/README.md). We reuse their maps instead of hand-authoring
addresses — that's the hard, error-prone part already done and verified.

Note on addressing: stable-retro data.json addresses are SYSTEM BUS addresses
(e.g. SMB lives = 1882 = NES CPU $075A; Sonic zone = $FFFE10 on the Genesis 68k
bus; SMW lives = $7E0DBE on the SNES bus). RetroArch's `READ_CORE_RAM` uses the
RetroAchievements address space instead, so we translate per console (windows
verified against rcheevos src/rcheevos/consoleinfo.c — see RAM_WINDOWS). Fields
whose bus address falls outside the console's mapped RAM window can't be reached
via READ_CORE_RAM; those keep the bus address and get a per-field
read_method="core_memory" fallback (needs a core that exposes a memory map).
Unknown consoles get no translation: the whole profile falls back to
read_method="core_memory" with raw bus addresses.

Usage:
    python3 tools/import_stable_retro.py <stable-retro-game-dir> [-o profiles/<name>.json]
    # e.g. .../site-packages/retro/data/stable/Airstriker-Genesis/

Find your install dir with:  python3 -c "import retro,os;print(os.path.join(os.path.dirname(retro.__file__),'data','stable'))"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# System-bus -> READ_CORE_RAM (RetroAchievements address space) translation.
# Each entry: list of (bus_start, bus_end, ra_start) windows. Verified against
# rcheevos src/rcheevos/consoleinfo.c (the table RetroArch uses for the
# achievement address space) on 2026-06-06:
#   NES        {0x0000,   0x07FF,   real 0x0000}   -> identity for CPU RAM
#   SNES       {0x000000, 0x01FFFF, real 0x7E0000} -> subtract 0x7E0000
#   Genesis    {0x000000, 0x00FFFF, real 0xFF0000} -> subtract 0xFF0000
#   Atari2600  {0x000000, 0x00007F, real 0x000080} -> subtract 0x80 (RIOT RAM)
#   GameBoy    rcheevos maps the WHOLE native bus identity (work RAM C000-DFFF
#              -> RA 0xC000-0xDFFF, HRAM FF80 -> RA 0xFF80, etc.)
#   SMS        {0x0000,   0x1FFF,   real 0xC000}   -> subtract 0xC000 (Z80 work RAM)
RAM_WINDOWS = {
    "nes":       [(0x0000, 0x07FF, 0x0000)],
    "snes":      [(0x7E0000, 0x7FFFFF, 0x0000)],
    "genesis":   [(0xFF0000, 0xFFFFFF, 0x0000)],
    "atari2600": [(0x0080, 0x00FF, 0x0000)],
    "gameboy":   [(0x0000, 0xFFFF, 0x0000)],
    "sms":       [(0xC000, 0xDFFF, 0x0000)],
}

# stable-retro system tokens (dir-name suffix like "...-Nes" / "...-GameBoy-v0",
# or metadata.json "system") -> RAM_WINDOWS keys.
SYSTEM_KEYS = {
    "nes": "nes",
    "snes": "snes",
    "genesis": "genesis", "megadrive": "genesis",
    "atari2600": "atari2600",
    "gameboy": "gameboy", "gb": "gameboy",
    "sms": "sms", "mastersystem": "sms",
}


def system_from_game_name(game_name: str) -> str:
    """'SuperMarioBros-Nes' / 'Asteroids-GameBoy-v0' -> 'Nes' / 'GameBoy'."""
    base = re.sub(r"-v\d+$", "", game_name)
    return base.rsplit("-", 1)[1] if "-" in base else ""


def translate_address(system_key: str, bus_addr: int):
    """System-bus address -> READ_CORE_RAM address, or None if the address has
    no mapping in that console's achievement address space."""
    for start, end, ra_start in RAM_WINDOWS.get(system_key, ()):
        if start <= bus_addr <= end:
            return bus_addr - start + ra_start
    return None


def convert(game_dir: str) -> dict:
    data_path = os.path.join(game_dir, "data.json")
    if not os.path.isfile(data_path):
        raise SystemExit(f"no data.json in {game_dir}")
    with open(data_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    info = data.get("info", {})
    if not info:
        raise SystemExit(f"{data_path} has no 'info' section")

    game_name = os.path.basename(os.path.normpath(game_dir))
    system = ""
    meta_path = os.path.join(game_dir, "metadata.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                system = json.load(fh).get("system", "") or ""
        except (json.JSONDecodeError, OSError):
            pass
    if not system:
        # stable-retro metadata.json usually has no "system"; the dir-name
        # suffix is the canonical place it lives.
        system = system_from_game_name(game_name)
    sys_key = SYSTEM_KEYS.get(system.lower(), "")

    fields = []
    unmapped = 0
    for name, spec in info.items():
        if "address" not in spec or "type" not in spec:
            continue
        bus_addr = int(spec["address"])         # decimal SYSTEM BUS address
        field = {
            "name": name,
            "address": bus_addr,
            "type": spec["type"],               # stable-retro descriptor, e.g. ">u4", ">n6"
        }
        if sys_key:
            ra_addr = translate_address(sys_key, bus_addr)
            if ra_addr is not None:
                field["address"] = ra_addr      # translated to READ_CORE_RAM space
            else:
                field["read_method"] = "core_memory"   # bus addr, outside RA window
                unmapped += 1
        fields.append(field)
    fields.sort(key=lambda f: f["name"])

    if sys_key:
        addr_note = (f"Bus addresses translated to the READ_CORE_RAM/achievement "
                     f"space ('{sys_key}' window per rcheevos consoleinfo.c)"
                     + (f"; {unmapped} field(s) outside the RAM window kept their "
                        f"bus address with read_method=core_memory" if unmapped else "")
                     + ".")
    else:
        addr_note = (f"Unknown system '{system}' — no bus->READ_CORE_RAM translation "
                     f"known, so addresses are raw system-bus addresses read via "
                     f"READ_CORE_MEMORY (requires a core that exposes a memory map).")

    return {
        "name": game_name.replace("-", " "),
        "system": system,
        "core": "",
        "read_method": "core_ram" if sys_key else "core_memory",
        "endian": "little",   # per-field descriptors carry their own endianness
        "match": {"game_substr": game_name.split("-")[0].lower(), "crc": ""},
        "notes": f"Imported from stable-retro '{game_name}'. {addr_note} Verify "
                 f"the core exposes RAM to the Network Command Interface.",
        "fields": fields,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir", help="stable-retro game directory (contains data.json)")
    ap.add_argument("-o", "--out", help="output profile path (default: stdout)")
    a = ap.parse_args(argv)
    profile = convert(a.game_dir)
    text = json.dumps(profile, indent=2)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {a.out} ({len(profile['fields'])} fields)", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
