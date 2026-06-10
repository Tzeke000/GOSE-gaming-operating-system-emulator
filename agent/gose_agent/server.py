"""Asyncio TCP server speaking the JSON-lines protocol.

Same protocol over Wi-Fi/Ethernet and USB-net. Per-message token auth for
non-loopback peers. Blocking capability work (subprocess, sleeps) is offloaded to
a thread executor so the event loop stays responsive. Events are broadcast to all
connected clients.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import time
import urllib.request
from typing import Optional, Set, Tuple

from .agent import Agent
from .config import AgentConfig
from . import protocol as P

log = logging.getLogger("gose.agent")

# ---- AI permission tiers (docs/16-ai-permission-model): observe < play < admin. ----
# The dev/owner token (config.token) and dev-loopback always resolve to 'admin' so OUR
# own MCP/dev connection stays fully privileged (back-compat). A downloader's AI connects
# with its own token, which maps to a granted tier; unmapped ops require admin (deny-by-default).
TIER_RANK = {"observe": 0, "play": 1, "admin": 2}
OP_TIER = {
    "ping": "observe", "agent.info": "observe",
    "system.status": "observe",
    "games.systems": "observe", "games.list": "observe",
    "screen.capture": "observe",
    "state.profiles": "observe", "state.attach": "observe", "state.read": "observe",
    "state.status": "observe", "state.read_raw": "observe",
    # play-map registry (#117) — read-only, observe tier
    "games.playmaps": "observe", "games.playmap": "observe",
    "input.button": "play", "input.combo": "play", "input.axis": "play", "input.type": "play",
    "input.seats": "observe", "input.seat_open": "play", "input.seat_close": "play",
    # host-pad passthrough (physical-controller event forwarding) — same tier as
    # the other input ops; pt_list is read-only.
    "input.pt_open": "play", "input.pt_event": "play", "input.pt_close": "play",
    "input.pt_list": "observe",
    "games.launch": "play", "games.stop": "play", "state.write_raw": "play",
    "system.run": "admin", "system.service": "admin",
    # push primitive: an armed AI holds this open to learn immediately when the owner
    # arms or disarms it, without polling /ai/play/queue over HTTP.
    "play.wait": "observe",
}
_AI_TOKENS_PATH = os.environ.get("GOSE_AGENT_AI_TOKENS", "/userdata/system/gose/ai_tokens.json")
# Same path the VM server writes; agent reads it to deliver play.wait events.
_AI_PLAY_ARM_F = "/userdata/system/gose/ai_play_arm.json"


def _read_arm_record() -> dict:
    """Return the arm record dict, or {} on missing/corrupt."""
    try:
        with open(_AI_PLAY_ARM_F, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


async def _play_wait(caller_name: str, args: dict) -> dict:
    """Async long-poll: resolves immediately on state change; times out to 'idle'.

    The caller holds this open after connecting so the armed/released event arrives
    as a push instead of a poll.  Must stay async (asyncio.sleep) so it doesn't
    block the event loop or other connections.
    """
    timeout_ms = int(args.get("timeout_ms", 25000))
    since = str(args.get("since", ""))
    deadline = time.monotonic() + timeout_ms / 1000.0
    poll_interval = 0.5

    def _sample():
        """Return (armed, rev, system, game, seat, playing, has_map) for caller_name."""
        rec = _read_arm_record()
        if rec and rec.get("name") == caller_name:
            try:
                rev = str(rec.get("ts", ""))
            except Exception:
                rev = ""
            return (True, rev, rec.get("system", ""), rec.get("game", ""),
                    rec.get("seat"), bool(rec.get("playing", False)),
                    bool(rec.get("has_map", False)))
        return False, "0", "", "", None, False, False

    armed, rev, system, game, seat, playing, has_map = _sample()

    while True:
        if rev != since:
            # State changed (or first call with no "since") — report it immediately.
            if armed:
                return {"event": "armed", "system": system, "game": game,
                        "seat": seat, "playing": playing, "has_map": has_map,
                        "rev": rev}
            else:
                return {"event": "released", "rev": "0"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"event": "idle", "rev": rev}
        await asyncio.sleep(min(poll_interval, remaining))
        armed, rev, system, game, seat, playing, has_map = _sample()


def _load_ai_tokens() -> dict:
    """token -> {"name": str, "tier": "observe|play|admin"}. Re-read each call so an
    owner's grant/revoke takes effect immediately (re-check-every-time, not cached)."""
    try:
        with open(_AI_TOKENS_PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ---- Audit log: every op a GUEST AI runs (per-AI token) is recorded, allowed or denied.
# Dev/owner-token + open-loopback ops are NOT logged — this audits guest AIs, not the owner.
# JSON-lines so the UI server can tail it cheaply (vm_server GET /ai/audit).
_AUDIT_PATH = os.environ.get("GOSE_AGENT_AI_AUDIT", "/userdata/system/gose/ai_audit.jsonl")
_AUDIT_MAX = 512 * 1024   # rotate: rename to .1 and start fresh past this size


def audit_append(name: str, op: str, ok: bool, code: Optional[str] = None,
                 path: Optional[str] = None) -> None:
    """Append one {ts,name,op,ok[,code]} line. Best-effort: an audit failure must
    never take down the request path (log it, drop the line)."""
    p = path or _AUDIT_PATH
    rec = {"ts": int(time.time()), "name": name, "op": op, "ok": bool(ok)}
    if code:
        rec["code"] = code
    try:
        try:
            if os.path.getsize(p) > _AUDIT_MAX:
                os.replace(p, p + ".1")
        except OSError:
            pass                      # missing file / first write — fine
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception as e:            # noqa: BLE001
        log.warning("audit write failed: %s", e)


# ---- Pre-auth pairing request: an unauthenticated client may ask the owner for access.
# Forwarded to the in-VM UI server (which stores it for the AI Hub's approve/deny banner).
# It NEVER grants anything by itself; rate-limited so a stranger can't spam the owner.
_PAIR_URL = os.environ.get("GOSE_AGENT_PAIR_URL", "http://127.0.0.1:8780/ai/request")
_PAIR_TIMES: list = []


def _pair_rate_ok(limit: int = 5, window: float = 60.0) -> bool:
    now = time.time()
    while _PAIR_TIMES and _PAIR_TIMES[0] < now - window:
        _PAIR_TIMES.pop(0)
    if len(_PAIR_TIMES) >= limit:
        return False
    _PAIR_TIMES.append(now)
    return True


def _forward_pair_request(args: dict) -> dict:
    """Blocking HTTP POST to the UI server's /ai/request (run off the event loop)."""
    body = json.dumps({"name": args.get("name"), "tier": args.get("tier", "observe")}).encode()
    req = urllib.request.Request(_PAIR_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class AgentServer:
    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()
        self._writers: Set[asyncio.StreamWriter] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Agent emits events; we fan them out to connected clients.
        self.agent = Agent(self.config, emit=self._emit)

    # ---- event fan-out (thread-safe-ish: scheduled on the loop) ----
    def _emit(self, name: str, data: dict):
        if not self._loop:
            return
        msg = P.encode(P.event(name, data))
        for w in list(self._writers):
            try:
                w.write(msg)
            except Exception:
                pass

    @staticmethod
    def _is_loopback(peer) -> bool:
        try:
            return ipaddress.ip_address(peer[0]).is_loopback
        except Exception:
            return False

    def _resolve_tier(self, peer, msg: dict) -> Tuple[Optional[str], Optional[str]]:
        """(tier, ai_name) for this request; (None, None) if unauthenticated.
        The dev/owner token and dev-loopback resolve to 'admin' (keeps OUR own MCP/dev
        connection fully privileged) with ai_name None — those are NOT audited.
        A per-AI token maps to its granted tier + name, which IS audited."""
        tok = msg.get("token")
        if self.config.token and tok == self.config.token:
            return "admin", None                 # developer/owner token — full access, always
        if self._is_loopback(peer) and not self.config.token:
            return "admin", None                 # dev convenience: open loopback, no token configured
        ai = _load_ai_tokens().get(tok or "")
        if isinstance(ai, dict) and ai.get("tier") in TIER_RANK:
            return ai["tier"], str(ai.get("name") or "unknown")
        return None, None

    @staticmethod
    def _pin_seat(msg: dict, args: dict) -> dict:
        """Seat arbitration: an AI token with an assigned seat may only drive THAT seat.
        Its input.* calls are pinned to the assignment (whatever seat it asked for), and
        it may not open/close other seats. Admin/dev tokens are unrestricted — the owner
        assigns seats in the AI Hub. Identity-blind dispatch stays identity-blind: the
        pinning happens here at the auth boundary."""
        ai = _load_ai_tokens().get(msg.get("token") or "")
        seat = ai.get("seat") if isinstance(ai, dict) else None
        if seat is None:
            return args
        op = msg.get("op") or ""
        if op in ("input.button", "input.combo", "input.axis", "input.type"):
            # Reject only an EXPLICIT cross-seat request; an omitted seat means
            # "my assigned seat" (the common drive pattern) and is pinned below —
            # defaulting omit to seat 1 would wrongly deny a seat>=2 AI's own input.
            if "seat" in args and int(args["seat"]) != int(seat):
                raise P.AgentError(P.ERR_DENIED,
                    f"this AI is assigned seat {seat}; it cannot send input to another seat")
            args = dict(args); args["seat"] = int(seat)
        elif op in ("input.seat_open", "input.seat_close"):
            if int(args.get("seat", 0)) != int(seat):
                raise P.AgentError(P.ERR_DENIED,
                    f"this AI is assigned seat {seat}; it cannot manage other seats")
        elif op in ("input.pt_open", "input.pt_event", "input.pt_close"):
            # Passthrough pads mirror the HUMAN's physical controller (the host
            # forwarder uses the owner/dev token). A seat-assigned guest AI has no
            # business creating or driving them.
            raise P.AgentError(P.ERR_DENIED,
                "this AI is assigned a seat; passthrough pads are host/owner-managed")
        return args

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        self._writers.add(writer)
        log.info("client connected: %s", peer)
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                req_id = None
                ai_name = None      # set for per-AI tokens only; gates the audit log
                op = None
                try:
                    msg = P.decode_line(line.decode("utf-8", "replace"))
                    req_id = msg.get("id")
                    op = msg.get("op")
                    tier, ai_name = self._resolve_tier(peer, msg)
                    if tier is None:
                        # Pre-auth pairing: 'pair.request' is the ONE op allowed without a
                        # token — it only files a request for the owner to approve in the
                        # AI Hub; it grants nothing. Rate-limited; connection stays open
                        # so the client sees the response.
                        if op == "pair.request":
                            if not _pair_rate_ok():
                                writer.write(P.encode(P.err_response(
                                    req_id, P.ERR_DENIED, "pairing requests rate-limited — try again in a minute")))
                            else:
                                try:
                                    r = await asyncio.get_event_loop().run_in_executor(
                                        None, _forward_pair_request, msg.get("args") or {})
                                    if r.get("ok"):
                                        writer.write(P.encode(P.ok_response(req_id, {
                                            "requested": True, "name": r.get("name"), "tier": r.get("tier"),
                                            "note": "request filed — the device owner approves it in GOSE → AI Hub"})))
                                    else:
                                        writer.write(P.encode(P.err_response(
                                            req_id, P.ERR_BADREQ, str(r.get("error") or "request rejected"))))
                                except Exception as e:  # noqa: BLE001 — UI server down/unreachable
                                    writer.write(P.encode(P.err_response(
                                        req_id, P.ERR_BACKEND, f"pairing service unavailable: {e}")))
                            await writer.drain()
                            continue
                        writer.write(P.encode(P.err_response(
                            req_id, P.ERR_AUTH,
                            "bad or missing token — the device owner grants access in GOSE → AI Hub "
                            "(Pairing shows your token), or send op 'pair.request' {name, tier} to ask "
                            "the owner to approve your pairing request")))
                        await writer.drain()
                        break
                    if not isinstance(op, str):
                        raise P.AgentError(P.ERR_BADREQ, "missing 'op'")
                    need = OP_TIER.get(op, "admin")     # unmapped ops require admin (deny-by-default)
                    if TIER_RANK[tier] < TIER_RANK[need]:
                        # Scope denial flows through the normal error path → connection stays open,
                        # so an Observe-tier AI can still do its allowed ops.
                        raise P.AgentError(P.ERR_DENIED,
                            f"'{op}' needs '{need}' access; this connection has '{tier}'. "
                            f"The device owner grants access in GOSE → AI Hub.")
                    args = self._pin_seat(msg, msg.get("args") or {})
                    if op == "agent.info":
                        # Augment the static info dict with the per-request tier + name so an AI
                        # can self-identify ("am I observe or play?") without provoking ERR_DENIED.
                        result = self.agent.info()
                        result["caller_tier"] = tier
                        result["caller_name"] = ai_name  # None for admin/dev tokens
                    elif op == "play.wait":
                        # Async long-poll: must NOT be offloaded to the executor (it sleeps
                        # on asyncio.sleep, which would block the thread pool).  Resolve the
                        # caller's name from the token — the ai_name field is set only for
                        # per-AI tokens; admin/dev connections get None, which is fine (they
                        # can still call play.wait using their own token lookup).
                        caller_name = ai_name
                        if caller_name is None:
                            # admin / dev token — resolve name the same way _load_ai_tokens does
                            ai_rec = _load_ai_tokens().get(msg.get("token") or "")
                            caller_name = str(ai_rec.get("name")) if isinstance(ai_rec, dict) else None
                        if not caller_name:
                            raise P.AgentError(P.ERR_DENIED,
                                "play.wait requires a per-AI token with a registered name")
                        result = await _play_wait(caller_name, args)
                    else:
                        # Run (possibly blocking) dispatch off the event loop.
                        result = await asyncio.get_event_loop().run_in_executor(
                            None, self.agent.dispatch, op, args)
                    writer.write(P.encode(P.ok_response(req_id, result)))
                    if ai_name:
                        audit_append(ai_name, op, True)
                except P.AgentError as e:
                    writer.write(P.encode(P.err_response(req_id, e.code, e.message)))
                    if ai_name and isinstance(op, str):
                        audit_append(ai_name, op, False, e.code)
                except Exception as e:  # noqa: BLE001 — never let one bad msg kill the conn
                    log.exception("dispatch error")
                    writer.write(P.encode(P.err_response(req_id, P.ERR_BACKEND, str(e))))
                    if ai_name and isinstance(op, str):
                        audit_append(ai_name, op, False, P.ERR_BACKEND)
                await writer.drain()
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
            log.info("client disconnected: %s", peer)

    async def _vc_ensure_loop(self):
        """Background task: ensure a virtual controller exists for every seated AI token.

        Reads ai_tokens.json every 4 s, collects distinct seat values, and calls
        seat_open for each seat not yet open.  seat_open is idempotent — if the VC
        already exists it returns immediately without recreating the pad.  Errors in
        any iteration are caught so a transient read failure never kills the loop or
        the event loop.
        """
        while True:
            try:
                tokens = _load_ai_tokens()
                seats_needed: set = set()
                for rec in tokens.values():
                    if isinstance(rec, dict):
                        s = rec.get("seat")
                        if s is not None:
                            try:
                                seats_needed.add(int(s))
                            except (TypeError, ValueError):
                                pass
                open_seats: set = set(self.agent.input._seats.keys())
                for seat in seats_needed:
                    if seat not in open_seats:
                        try:
                            self.agent.input.seat_open(seat)
                            log.info("vc_ensure: opened VC for seat %d", seat)
                        except Exception as exc:
                            log.warning("vc_ensure: seat_open(%d) failed: %s", seat, exc)
            except Exception as exc:
                log.warning("vc_ensure: iteration error: %s", exc)
            await asyncio.sleep(4)

    async def serve(self):
        self._loop = asyncio.get_event_loop()
        server = await asyncio.start_server(self._handle, self.config.host, self.config.port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        log.info("GOSE Agent listening on %s", addrs)
        asyncio.ensure_future(self._vc_ensure_loop())
        async with server:
            await server.serve_forever()

    def run(self):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        b = self.agent.backends()
        log.info("backends: %s", b)
        if not self.config.token:
            log.warning("no token set — only loopback clients allowed. "
                        "Set GOSE_AGENT_TOKEN for Wi-Fi/USB access.")
        try:
            asyncio.run(self.serve())
        except KeyboardInterrupt:
            log.info("shutting down")
