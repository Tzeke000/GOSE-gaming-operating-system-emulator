#!/usr/bin/env python3
"""GOSE Spectate Runner — two AI play loops running a 2P game head-to-head.

This script is launched by gose_vm_server.py's spectate_start() as a subprocess
on the HOST side (Windows/Linux — wherever the server runs). It:
  1. Connects to the GOSE agent (port 8731) with each AI's token.
  2. Opens seat 2 (seat 1 already exists).
  3. Attaches the game's RAM profile on both connections.
  4. Runs AI-A's play loop on seat 1 (sharp, 20Hz) and AI-B's on seat 2 (sleepy, 3Hz).
  5. Writes a PID file so the server can track and kill us cleanly.
  6. On exit (game over, stop signal, or error): releases all buttons and exits.

The play policy is the REAL policy from wren_vs_wren.py (proven 9-8 me-vs-me):
  Seat 1 (AI-A): sharp — retargets every tick (20Hz).
  Seat 2 (AI-B): sleepy — retargets at 3Hz (so the match ends, not an infinite rally).

Reuses: SeatManager / multiplayer seat pinning (agent pinned the token to each seat
before we launched — the server writes ai_tokens.json with seat=1 / seat=2 before
calling us). The RAM profile and score fields are the same verified pong1k2p.json.

Usage:
  python3 gose_spectate_runner.py
      --system nes --game pong1k2p
      --profile pong1k2p
      --token-a <token_a> --token-b <token_b>
      --agent-host 127.0.0.1 --agent-port 8731
      --pidfile /tmp/gose_spectate.pid
      --session-id <id>

Exit codes: 0 = normal (game over); 1 = startup error; 2 = stop requested (SIGTERM).
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time

# Allow running from the repo root, pc-image tree, OR the deployed VM location
# (/userdata/gose-ui/ on the device, where the agent is at /userdata/system/gose/agent/client).
_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.join(_HERE, "..", "..", "agent", "client"),           # repo: pc-image/gose-vm-host/ -> up2 -> agent/client
    os.path.join(_HERE, "..", "system", "gose", "agent", "client"),  # deployed: /userdata/gose-ui/ -> ../system/gose/agent/client
    "/userdata/system/gose/agent/client",                          # absolute VM path
]
_AGENT_CLIENT = next((p for p in _CANDIDATES if os.path.isdir(p)), _CANDIDATES[0])
sys.path.insert(0, os.path.normpath(_AGENT_CLIENT))

try:
    from gose_client import GoseClient, GoseClientError
except ImportError as e:
    print(f"ERROR: cannot import GoseClient from {_AGENT_CLIENT}: {e}", file=sys.stderr)
    sys.exit(1)

PADDLE_H = 0x20      # Pong paddle height: 32px (verified pong1k2p.json notes)
DEAD = 6             # px dead-zone around target
HZ = 20.0            # seat 1 (sharp) tick rate
SLEEPY_PERIOD = 0.33 # seat 2 re-targets this often (~3Hz)
GAME_OVER_SCORE = 9  # first to 9 (verified vs display)
FROZEN_TIMEOUT = 30.0 # seconds ball can be frozen before declaring stuck (game may need ~10s to start)
START_TAPS = 4        # tap start N times to ensure the game leaves attract mode

_stop = threading.Event()


def _write_pidfile(path: str):
    """Write our PID so the server can kill us cleanly."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print(f"WARN: could not write pidfile {path}: {e}", file=sys.stderr)


def _remove_pidfile(path: str):
    try:
        os.unlink(path)
    except Exception:
        pass


def _connect(host: str, port: int, token: str, profile: str) -> GoseClient:
    c = GoseClient(host, port, token=token, timeout=15)
    c.connect()
    c.call("state.attach", profile=profile)
    return c


def _state(c: GoseClient) -> dict:
    return c.call("state.read")["fields"]


def _btn(c: GoseClient, seat: int, name: str, action: str):
    c.call("input.button", button=name, action=action, seat=seat)


def _steer(c: GoseClient, seat: int, held: dict, pad_center: int, target: int):
    delta = target - pad_center
    want = "up" if delta < -DEAD else ("down" if delta > DEAD else None)
    if want != held[seat]:
        if held[seat]:
            _btn(c, seat, held[seat], "release")
        if want:
            _btn(c, seat, want, "press")
        held[seat] = want


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True)
    ap.add_argument("--game", required=True)
    ap.add_argument("--profile", default="pong1k2p")
    ap.add_argument("--token-a", required=True, dest="token_a")
    ap.add_argument("--token-b", required=True, dest="token_b")
    ap.add_argument("--agent-host", default="127.0.0.1", dest="agent_host")
    ap.add_argument("--agent-port", type=int, default=8731, dest="agent_port")
    ap.add_argument("--pidfile", default="/tmp/gose_spectate.pid")
    ap.add_argument("--session-id", default="", dest="session_id")
    args = ap.parse_args()

    _write_pidfile(args.pidfile)

    def _on_term(sig, frame):
        print("SPECTATE: SIGTERM — stopping.", file=sys.stderr)
        _stop.set()

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    # --- connect two clients ---
    try:
        print(f"SPECTATE: connecting AI-A (seat 1) token={args.token_a[:8]}…", file=sys.stderr)
        ca = _connect(args.agent_host, args.agent_port, args.token_a, args.profile)
    except Exception as e:
        print(f"SPECTATE: AI-A connect failed: {e}", file=sys.stderr)
        _remove_pidfile(args.pidfile)
        sys.exit(1)

    try:
        print(f"SPECTATE: connecting AI-B (seat 2) token={args.token_b[:8]}…", file=sys.stderr)
        cb = _connect(args.agent_host, args.agent_port, args.token_b, args.profile)
        # Open seat 2 (the server pinned token-b to seat=2, this just ensures the seat exists)
        cb.call("input.seat_open", seat=2)
    except Exception as e:
        print(f"SPECTATE: AI-B connect/seat failed: {e}", file=sys.stderr)
        ca.close()
        _remove_pidfile(args.pidfile)
        sys.exit(1)

    # --- (optional) reset and start ---
    # NCI RESET requires system.run which is admin-only. For spectate we skip the
    # reset (game was freshly launched by the server) and just tap start.
    # If the token has admin tier, the reset is attempted for a clean slate.
    try:
        # Try NCI RESET (admin-only; silently skip if play tier)
        try:
            ca.call("system.run", cmd="python3 /tmp/nci_probe.py cmd RESET", timeout_ms=8000)
            time.sleep(1.0)
            print("SPECTATE: NCI RESET done", file=sys.stderr)
        except GoseClientError as e:
            if "ERR_TIER" in str(e.code) or "insufficient" in str(e.message).lower() or "admin" in str(e.message).lower():
                print(f"SPECTATE: skipping NCI RESET (play tier, not admin): {e}", file=sys.stderr)
                time.sleep(0.5)
            else:
                # Other errors (backend down, NCI probe missing) — continue anyway
                print(f"SPECTATE: NCI RESET failed (non-fatal): {e}", file=sys.stderr)
                time.sleep(0.5)
        # Release any stuck buttons
        for seat, c in ((1, ca), (2, cb)):
            for b in ("up", "down"):
                try:
                    _btn(c, seat, b, "release")
                except Exception:
                    pass
        # Tap start on seat 1 multiple times to ensure the game leaves attract mode
        # (pong1k2p needs 1-2 start presses: title screen + rally start)
        for _ in range(START_TAPS):
            _btn(ca, 1, "start", "tap")
            time.sleep(0.6)
    except Exception as e:
        print(f"SPECTATE: game start buttons failed: {e}", file=sys.stderr)
        ca.close(); cb.close()
        _remove_pidfile(args.pidfile)
        sys.exit(1)

    # --- main play loop ---
    held = {1: None, 2: None}
    last_ball = None
    frozen_since = None
    last_sleepy = 0.0
    sleepy_target = 0x78 + PADDLE_H // 2
    score_a = score_b = 0
    t_tick = 1.0 / HZ
    outcome = "unknown"
    last_start_tap = time.time()
    game_started = False   # True once ball has moved away from 0,0
    print("SPECTATE: play loop running", file=sys.stderr)

    try:
        while not _stop.is_set():
            t0 = time.time()
            try:
                s = _state(ca)
            except Exception as e:
                print(f"SPECTATE: state.read error: {e}", file=sys.stderr)
                time.sleep(1.0)
                continue

            bx, by = s.get("ball_x", 0), s.get("ball_y", 0)
            score_a = s.get("score_left", 0)
            score_b = s.get("score_right", 0)

            # Game over check
            if score_a >= GAME_OVER_SCORE or score_b >= GAME_OVER_SCORE:
                winner = "AI-A" if score_a > score_b else "AI-B"
                outcome = f"{winner} wins {score_a}-{score_b}"
                print(f"SPECTATE: game over — {outcome}", file=sys.stderr)
                break

            # Frozen ball timeout
            ball_pos = (bx, by)
            if ball_pos == (0, 0) and not game_started:
                # Still in attract/countdown phase — keep tapping start every 2s
                if time.time() - last_start_tap > 2.0:
                    try:
                        _btn(ca, 1, "start", "tap")
                        last_start_tap = time.time()
                    except Exception:
                        pass
                # Don't start frozen timer until ball actually moves
                last_ball = ball_pos
            elif ball_pos == last_ball:
                if ball_pos != (0, 0):
                    game_started = True
                if frozen_since is None:
                    frozen_since = time.time()
                elif time.time() - frozen_since > FROZEN_TIMEOUT:
                    outcome = f"frozen {score_a}-{score_b}"
                    print(f"SPECTATE: ball frozen — {outcome}", file=sys.stderr)
                    break
            else:
                frozen_since = None
                last_ball = ball_pos
                if ball_pos != (0, 0):
                    game_started = True

            # Seat 1 (AI-A): sharp — every tick
            try:
                pad1_c = s.get("p1_paddle_y", 0x78) + PADDLE_H // 2
                _steer(ca, 1, held, pad1_c, by)
            except Exception as e:
                print(f"SPECTATE: seat1 steer error: {e}", file=sys.stderr)

            # Seat 2 (AI-B): sleepy — re-decides at 3Hz
            if time.time() - last_sleepy > SLEEPY_PERIOD:
                sleepy_target = by
                last_sleepy = time.time()
            try:
                pad2_c = s.get("p2_paddle_y", 0x78) + PADDLE_H // 2
                _steer(cb, 2, held, pad2_c, sleepy_target)
            except Exception as e:
                print(f"SPECTATE: seat2 steer error: {e}", file=sys.stderr)

            dt = time.time() - t0
            if dt < t_tick:
                _stop.wait(t_tick - dt)  # interruptible sleep

    finally:
        # --- cleanup: release all buttons ---
        for seat, c in ((1, ca), (2, cb)):
            for b in ("up", "down"):
                try:
                    _btn(c, seat, b, "release")
                except Exception:
                    pass
        try:
            ca.close()
        except Exception:
            pass
        try:
            cb.close()
        except Exception:
            pass
        _remove_pidfile(args.pidfile)

    print(f"SPECTATE: done — {outcome}", file=sys.stderr)
    exit_code = 2 if _stop.is_set() else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
