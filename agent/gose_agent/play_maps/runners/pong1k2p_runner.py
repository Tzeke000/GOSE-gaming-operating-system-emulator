#!/usr/bin/env python3
"""pong1k2p_runner.py — in-guest P2 autoplay loop for Pong 1K 2P (GOSE, NES).

WHY IN-GUEST: Driving the paddle via the remote MCP/agent (one input per
round-trip, ~1-2 s) cannot keep up with the ball, which crosses the screen in
~1 s.  Running this loop INSIDE the VM talks to the agent on localhost:8731
with sub-millisecond round-trips, achieving ~30 Hz — enough to track and
predict the ball.

ALGORITHM — predict, don't chase:
  Take two consecutive state reads to get ball velocity (vx, vy).  When the
  ball is heading toward the right paddle (vx > 0), project ball_y to the
  paddle's x-plane with top/bottom wall reflection (triangle-wave fold).
  Position the paddle center at that projected y.  Reactive tracking of live
  ball_y lags a fast ball and loses; prediction keeps up.

AUTH — this is critical:
  A guest-internal connection to the agent at 127.0.0.1:8731 is NOT
  auto-admin.  The "open loopback => admin" shortcut applies to the host/dev
  side only (when no token is configured).  A guest connection MUST send a
  valid token.  Tier matters: OBSERVE can read state but CANNOT send input —
  the paddle stays frozen.  Supply a PLAY or ADMIN token.

HOW TO RUN (inside the VM):
  GOSE_AI_TOKEN=<play-or-admin-token> python3 pong1k2p_runner.py
  or:
  python3 pong1k2p_runner.py <play-or-admin-token>

  The runner exits cleanly when a score hits 9 (game over).  Restart it to
  play another match (after the human presses Start to begin the new game —
  see RESTART note below).

RESTART NOTE:
  After game-over, to reset: send NCI RESET to RetroArch (UDP 127.0.0.1:55355,
  may need 2-4 sends), then the human (P1) taps Start.  gose_launch via the
  agent was dry-mode (no-op, pid -1) in the 2026-06-14 session — cannot
  reliably relaunch from the agent.  Start is P1-gated: the AI on P2 cannot
  begin a match from the title/game-over screen.
"""

import os
import sys
import socket
import json
import time

HOST = "127.0.0.1"
PORT = 8731

# Token from env, then argv, then fail clearly.
TOKEN = os.environ.get("GOSE_AI_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else None)
if not TOKEN:
    sys.exit(
        "ERROR: no auth token.\n"
        "Usage: GOSE_AI_TOKEN=<play-or-admin-token> python3 pong1k2p_runner.py\n"
        "       python3 pong1k2p_runner.py <play-or-admin-token>\n"
        "OBSERVE-tier tokens cannot send input — use PLAY or ADMIN."
    )

DEADZONE = 6      # px tolerance around target before nudging (anti-jitter)
LOOP_S   = 0.03   # ~33 Hz cadence

# Ball travel bounds for wall-bounce reflection (empirical, NES screen height)
Y_MIN = 8
Y_MAX = 200
# Approximate ball_x where the ball meets the right (P2) paddle
MY_X = 200


class _Agent:
    """Minimal JSON-line client for the GOSE agent protocol."""

    def __init__(self, host: str, port: int, token: str) -> None:
        self._s   = socket.create_connection((host, port), timeout=10)
        self._buf = b""
        self._seq = 0
        self._tok = token

    def call(self, op: str, **args):
        self._seq += 1
        req = {"id": self._seq, "op": op, "args": args, "token": self._tok}
        self._s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        while True:
            while b"\n" not in self._buf:
                chunk = self._s.recv(65536)
                if not chunk:
                    raise RuntimeError("agent connection closed")
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            msg = json.loads(line)
            if "event" in msg:
                continue  # skip unsolicited events
            if msg.get("id") != req["id"]:
                continue  # stale / out-of-order
            if not msg.get("ok"):
                raise RuntimeError(f"{msg.get('code')}: {msg.get('error')}")
            return msg.get("result", {})


def _predict_y(bx: float, by: float, vx: float, vy: float) -> float:
    """Project ball_y to MY_X with top/bottom wall reflection."""
    if vx <= 0:
        return by  # ball moving away; no useful prediction
    cycles = (MY_X - bx) / vx
    yp     = by + vy * cycles
    span   = Y_MAX - Y_MIN
    if span <= 0:
        return by
    # Triangle-wave fold: map yp into [Y_MIN, Y_MAX]
    t = (yp - Y_MIN) % (2 * span)
    if t < 0:
        t += 2 * span
    if t > span:
        t = 2 * span - t
    return Y_MIN + t


def main() -> None:
    agent = _Agent(HOST, PORT, TOKEN)

    try:
        agent.call("state.attach", profile="pong1k2p")
    except Exception as e:
        print(f"state.attach warning (non-fatal): {e}", flush=True)

    held = [None]  # currently held direction: None | "up" | "down"

    def setdir(d):
        if d == held[0]:
            return
        if held[0]:
            try:
                agent.call("input.button", button=held[0], action="release")
            except Exception:
                pass
        if d:
            try:
                agent.call("input.button", button=d, action="press")
            except Exception:
                pass
        held[0] = d

    print("pong1k2p_runner: loop started (P2 = right paddle)", flush=True)
    prev_bx: float | None = None
    prev_by: float | None = None

    try:
        while True:
            try:
                fields = agent.call("state.read", profile="pong1k2p").get("fields", {})
            except Exception as e:
                print(f"state.read error (retrying): {e}", flush=True)
                time.sleep(0.05)
                continue

            speed = fields.get("ball_speed", 0)
            sl    = fields.get("score_left",  0)
            sr    = fields.get("score_right", 0)

            # Idle during pre-serve or after game over
            if speed == 0 or sl >= 9 or sr >= 9:
                setdir(None)
                prev_bx = prev_by = None
                if sl >= 9 or sr >= 9:
                    print(f"pong1k2p_runner: game over ({sl}-{sr}), exiting", flush=True)
                    break
                time.sleep(0.12)
                continue

            bx = float(fields.get("ball_x", 0))
            by = float(fields.get("ball_y", 0))
            p2 = float(fields.get("p2_paddle_y", 0))
            center = p2 + 16  # paddle is 32 px tall

            # Prediction: use velocity when available
            if prev_bx is not None:
                vx = bx - prev_bx
                vy = by - prev_by
                target = _predict_y(bx, by, vx, vy)
            else:
                target = by  # first read: fall back to reactive

            prev_bx, prev_by = bx, by

            if target > center + DEADZONE:
                setdir("down")
            elif target < center - DEADZONE:
                setdir("up")
            else:
                setdir(None)

            time.sleep(LOOP_S)

    finally:
        setdir(None)  # always release any held input on exit
        print("pong1k2p_runner: done", flush=True)


if __name__ == "__main__":
    main()
