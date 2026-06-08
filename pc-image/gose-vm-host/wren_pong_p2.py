#!/usr/bin/env python3
"""Wren plays pong1k2p against a HUMAN, fully self-calibrating.

The seat/port mapping kept surprising us (config said one thing, the profile note
another), so this DISCOVERS the truth live instead of trusting any of it:
  - On the first serve it nudges my controller (the default js0 dev controller,
    driven by input.button with NO seat = exactly what gose_tap does) and watches
    which paddle (p1=left / p2=right) actually moves, AND which button moves it
    which way. Whatever paddle responds is MINE; the human has the other.
  - Then it tracks the ball with my paddle at a configurable difficulty level.
  - Auto-detects the serve (ball leaving 0,0) — no human cue (the lesson: know the
    game started by WATCHING it).
  - Handles game-over -> new game (re-discovers each game).
  - Reads difficulty from /play/difficulty every game start; Learning mode adapts
    based on history posted to /play/history.
  - On game-over: writes a .gameover flag file so the server skips the stale
    auto-save on next launch (#112).

Stop: delete D:\\gose-vm\\wren_pong_p2.run, or kill the process.
Log:  D:\\gose-vm\\wren_pong_p2.log
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

sys.path.insert(0, r"D:\GOSE-gaming-operating-system-emulator\agent\client")
from gose_client import GoseClient  # noqa: E402

TOKEN = os.environ.get("GOSE_TOKEN", "***REMOVED-DEV-TOKEN***")
HOST, PORT = "127.0.0.1", 8731
# NOTE: the UI server (8780) is VM-internal only; difficulty/history go through the agent
# via system.run so we can read/write the play_config.json file inside the VM.
PROFILE = "pong1k2p"
PADDLE_H = 0x20
GAME_OVER = 9
RUNFLAG = r"D:\gose-vm\wren_pong_p2.run"
LOGFILE = r"D:\gose-vm\wren_pong_p2.log"
SAVES_ROOT = "/userdata/saves"   # must match server constant
PLAY_CONFIG_F = "/userdata/system/gose/play_config.json"
SYSTEM = "nes"
GAME = "pong1k2p"

logging.basicConfig(filename=LOGFILE, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pong2")

# --- difficulty defaults ---
_DIFF_DEFAULTS = {
    "easy":     {"hz": 5,  "dead": 15},
    "med":      {"hz": 12, "dead": 8},
    "hard":     {"hz": 25, "dead": 3},
    "learning": {"hz": 12, "dead": 8},  # base; adapted via history
}

# GoseClient reference — set in main() once connected
_client = None


def _agent_run(cmd: str) -> dict:
    """Run a shell command inside the VM via the agent (read-only paths work fine)."""
    try:
        return _client.call("system.run", cmd=cmd, timeout_ms=5000)
    except Exception as e:
        return {"code": 1, "stdout": "", "stderr": str(e)}


def _fetch_difficulty() -> dict:
    """Read difficulty config from the VM via agent system.run (cat play_config.json)."""
    try:
        r = _agent_run(f"cat {PLAY_CONFIG_F} 2>/dev/null || echo '{{}}'")
        raw = (r.get("stdout") or "{}").strip()
        cfg = json.loads(raw)
        diff = cfg.get("difficulty", "med")
        if diff not in _DIFF_DEFAULTS:
            diff = "med"
        # Learning: compute params from history
        if diff == "learning":
            history = cfg.get("history", [])
            recent = [e for e in history[-20:] if isinstance(e, dict)]
            if len(recent) >= 3:
                wins = sum(1 for e in recent if e.get("score_human", 0) > e.get("score_ai", 0))
                t = max(0.0, min(1.0, wins / len(recent)))
                hz = round(5 + t * 20)
                dead = round(15 - t * 12)
            else:
                hz, dead = 6, 13   # calibrating: start easy
        else:
            hz = _DIFF_DEFAULTS[diff]["hz"]
            dead = _DIFF_DEFAULTS[diff]["dead"]
        log.info("difficulty: %s (hz=%s dead=%s)", diff, hz, dead)
        return {"hz": hz, "dead": dead, "diff": diff}
    except Exception as e:
        log.warning("could not fetch difficulty (using med defaults): %s", e)
        return {"hz": 12, "dead": 8, "diff": "med"}


def _post_history(score_human: int, score_ai: int, difficulty: str):
    """Append a game result to play_config.json via agent (uses server's /play/history)."""
    try:
        # Write history by calling the server via curl from inside the VM
        entry_json = json.dumps({
            "score_human": score_human, "score_ai": score_ai,
            "difficulty": difficulty, "ts": time.time()
        })
        safe = entry_json.replace("'", "'\"'\"'")
        r = _agent_run(f"echo '{safe}' > /tmp/play_hist_entry.json && "
                       f"curl -s -X POST http://127.0.0.1:8780/play/history "
                       f"-H 'Content-Type: application/json' -d @/tmp/play_hist_entry.json")
        log.info("play history posted: human=%d ai=%d diff=%s | %s",
                 score_human, score_ai, difficulty, r.get("stdout","").strip()[:80])
    except Exception as e:
        log.warning("play history post failed: %s", e)


def _write_gameover_flag(score_left: int, score_right: int):
    """Write a .gameover flag into the VM so the server deletes the stale auto-save on next launch (#112).
    The flag must live inside the VM (not the Windows host), so we write it via the agent's
    curl-to-server pattern (same path as _post_history)."""
    try:
        path = f"{SAVES_ROOT}/{SYSTEM}/{GAME}.gameover"
        flag_json = json.dumps({"score_left": score_left, "score_right": score_right,
                                "ts": time.time(), "source": "wren_pong_p2"})
        safe = flag_json.replace("'", "'\"'\"'")
        r = _agent_run(f"mkdir -p {SAVES_ROOT}/{SYSTEM} && echo '{safe}' > {path} && echo FLAG_OK")
        if "FLAG_OK" in (r.get("stdout") or ""):
            log.info("#112 gameover flag written in VM: %s (score %d-%d)", path, score_left, score_right)
        else:
            log.warning("#112 gameover flag write failed: %s", r)
    except Exception as e:
        log.warning("#112 gameover flag write failed: %s", e)


def main():
    global _client
    try:
        open(RUNFLAG, "w").write("run")
    except Exception:
        pass

    c = GoseClient(HOST, PORT, token=TOKEN, timeout=15)
    c.connect()
    _client = c   # make available to helpers before the loop
    c.call("state.attach", profile=PROFILE)
    log.info("connected (driving js0 dev controller, no seat = gose_tap path); watching for the serve")

    def st():
        return c.call("state.read")["fields"]

    def press(b):
        c.call("input.button", button=b, action="press")

    def release(b):
        c.call("input.button", button=b, action="release")

    def release_all():
        for b in ("up", "down"):
            try: release(b)
            except Exception: pass

    # EMPIRICAL (verified live 2026-06-08, Zeke's paddle held static): my js0 input drives
    # the RIGHT paddle (p2_paddle_y moved 120->71 while p1 stayed); "up" DECREASES its y.
    # Zeke is on the LEFT (p1). So track p2, up_sign=-1.
    my_field = "p2_paddle_y"
    up_sign = -1

    # --- ball-trajectory PREDICTION (Zeke's coaching: don't just track, predict + pre-position).
    # Learn the right-wall/paddle x-plane + the top/bottom y-bounds live; project the ball there.
    pred = {"x": None, "y": None, "mx": 220, "ylo": 80, "yhi": 200}

    def fold(y, lo, hi):
        """Mirror-fold y into [lo,hi] — models the ball bouncing off top/bottom walls."""
        if hi <= lo:
            return y
        span = hi - lo
        t = (y - lo) % (2 * span)
        return lo + (t if t <= span else 2 * span - t)

    def predict_target(bx, by):
        p = pred
        p["mx"] = max(p["mx"], bx)                      # my paddle's x-plane (max ball x)
        p["ylo"] = min(p["ylo"], by); p["yhi"] = max(p["yhi"], by)
        vx = (bx - p["x"]) if p["x"] is not None else 0
        vy = (by - p["y"]) if p["y"] is not None else 0
        p["x"], p["y"] = bx, by
        if vx > 0.5 and p["mx"] > bx:                   # ball coming toward me -> intercept
            steps = (p["mx"] - bx) / vx
            return fold(by + vy * steps, p["ylo"], p["yhi"])
        return (p["ylo"] + p["yhi"]) / 2                # ball moving away -> rest at center

    # Load difficulty at startup (will reload each new game)
    diff_cfg = _fetch_difficulty()
    HZ = diff_cfg["hz"]
    DEAD = diff_cfg["dead"]
    current_diff = diff_cfg["diff"]

    held = None
    last_live = False
    last_log = 0.0
    tick = 1.0 / HZ
    # Score tracking for history posting
    game_sl = game_sr = 0
    # Guard: post history + gameover flag exactly once per game (not on every 3s tick)
    history_posted = False

    try:
        while os.path.exists(RUNFLAG):
            t0 = time.time()
            try:
                s = st()
            except Exception as e:
                log.warning("state.read error: %s", e); time.sleep(0.5); continue
            bx, by = s.get("ball_x", 0), s.get("ball_y", 0)
            sl, sr = s.get("score_left", 0), s.get("score_right", 0)
            live = (bx, by) != (0, 0) and sl < GAME_OVER and sr < GAME_OVER
            over = sl >= GAME_OVER or sr >= GAME_OVER

            if live and not last_live:
                # New game detected — reload difficulty so Learning can adapt
                diff_cfg = _fetch_difficulty()
                HZ = diff_cfg["hz"]
                DEAD = diff_cfg["dead"]
                current_diff = diff_cfg["diff"]
                tick = 1.0 / HZ
                # Reset prediction state and history guard for the new game
                pred.update({"x": None, "y": None, "mx": 220, "ylo": 80, "yhi": 200})
                history_posted = False
                log.info("SERVE detected (ball %d,%d) — playing RIGHT paddle, score %d-%d | %s hz=%s dead=%s",
                         bx, by, sl, sr, current_diff, HZ, DEAD)

            if not live and last_live:
                # Game just ended (ball went idle, score transition, or game-over)
                game_sl, game_sr = sl, sr
                release_all(); held = None
                pred["x"] = pred["y"] = None   # reset velocity tracking between rallies
            last_live = live

            if live:
                target = predict_target(bx, by)
                center = s.get(my_field, 0x78) + PADDLE_H // 2
                delta = target - center
                if abs(delta) <= DEAD:
                    want = None
                else:
                    want = "up" if (delta > 0) == (up_sign > 0) else "down"
                if want != held:
                    if held:
                        try: release(held)
                        except Exception: pass
                    if want:
                        try: press(want)
                        except Exception: pass
                    held = want
                if time.time() - last_log > 4:
                    log.info("playing RIGHT (predict): ball=%d,%d -> target=%d my_paddle=%d score %d-%d | %s",
                             bx, by, int(target), s.get(my_field, 0), sl, sr, current_diff)
                    last_log = time.time()

            if over:
                release_all(); held = None
                if not history_posted:
                    # I'm RIGHT = score_right; Zeke is LEFT = score_left
                    who = "I (Wren)" if sr > sl else "Zeke"
                    log.info("GAME OVER %d-%d — %s won. | diff=%s", sl, sr, who, current_diff)
                    last_log = time.time()
                    # Record history exactly once per game (human=sl=LEFT=Zeke; ai=sr=RIGHT=Wren)
                    _post_history(score_human=sl, score_ai=sr, difficulty=current_diff)
                    # #112: write gameover flag so the server deletes the stale auto-save on next launch
                    _write_gameover_flag(score_left=sl, score_right=sr)
                    history_posted = True

            dt = time.time() - t0
            if dt < tick:
                time.sleep(tick - dt)
    finally:
        release_all()
        try: c.close()
        except Exception: pass
        log.info("stopped")


if __name__ == "__main__":
    main()
