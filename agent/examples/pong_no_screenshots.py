#!/usr/bin/env python3
"""Demo: an AI plays Pong using ONLY memory state — no screenshots.

This is the "Mineflayer for retro" idea made tangible. It runs a tiny Pong sim
behind a *mock* RetroArch Network Command Interface (UDP), then drives a paddle
with the real GOSE game-state capability:

    loop:  read ball_y from memory  ->  decide  ->  move paddle

On a real Odin 2 the only things that change are (a) RetroArch is real, and
(b) "move paddle" becomes input.button injection instead of a memory write.
Run from agent/:   python3 examples/pong_no_screenshots.py
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gose_agent.capabilities.gamestate import GameStateCapability  # noqa: E402

H = 200  # play-field height

# Memory layout (little-endian s16): ball_x, ball_y, ball_vy, paddle_y, score, miss
ADDR = {"ball_x": 0x00, "ball_y": 0x02, "ball_vy": 0x04,
        "paddle_y": 0x06, "score": 0x08, "miss": 0x0A}


class PongRetroArch:
    """Mock RetroArch UDP server running a minimal Pong physics loop."""

    def __init__(self):
        self.mem = bytearray(0x20)
        self._set("ball_x", 100); self._set("ball_y", 100)
        self._set("ball_vy", 3); self._set("paddle_y", 100)
        self.vx = -4  # ball heading toward our paddle (x=0)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0)); self.port = self.sock.getsockname()[1]
        self._run = True
        threading.Thread(target=self._net, daemon=True).start()
        threading.Thread(target=self._physics, daemon=True).start()

    def _set(self, k, v): self.mem[ADDR[k]:ADDR[k] + 2] = struct.pack("<H", v & 0xFFFF)
    def _get(self, k): return struct.unpack("<h", self.mem[ADDR[k]:ADDR[k] + 2])[0]

    def _physics(self):
        while self._run:
            time.sleep(0.03)
            x = self._get("ball_x") + self.vx
            y = self._get("ball_y") + self._get("ball_vy")
            if y <= 0 or y >= H:                       # bounce off top/bottom
                self._set("ball_vy", -self._get("ball_vy")); y = max(0, min(H, y))
            if x <= 0:                                  # reached our paddle
                if abs(y - self._get("paddle_y")) <= 20:
                    self.vx = abs(self.vx); x = 0
                    self._set("score", self._get("score") + 1)
                else:
                    self._set("miss", self._get("miss") + 1)
                    x = 100; y = 100                    # serve again
            elif x >= 200:                              # far wall: bounce back
                self.vx = -abs(self.vx); x = 200
            self._set("ball_x", x); self._set("ball_y", y)

    def _net(self):
        self.sock.settimeout(0.2)
        while self._run:
            try:
                data, addr = self.sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            p = data.decode().split(); cmd = p[0]
            if cmd in ("READ_CORE_MEMORY", "READ_CORE_RAM"):
                a = int(p[1], 16); n = int(p[2])
                hexb = " ".join(f"{b:02x}" for b in self.mem[a:a + n])
                self.sock.sendto(f"{cmd} {p[1]} {hexb}".encode(), addr)
            elif cmd in ("WRITE_CORE_MEMORY", "WRITE_CORE_RAM"):
                a = int(p[1], 16); vals = [int(b, 16) for b in p[2:]]
                self.mem[a:a + len(vals)] = bytes(vals)
                self.sock.sendto(f"{cmd} {p[1]} {len(vals)}".encode(), addr)
            else:
                self.sock.sendto(f"{cmd} -1 unknown".encode(), addr)

    def stop(self): self._run = False; self.sock.close()


def main(frames: int = 120):
    ra = PongRetroArch()
    # A profile describing where the state lives (little-endian s16 fields).
    profiles_dir = tempfile.mkdtemp()
    with open(os.path.join(profiles_dir, "pong.json"), "w") as fh:
        json.dump({
            "name": "Pong (demo)", "system": "demo", "endian": "little",
            "read_method": "core_memory", "match": {"game_substr": "pong"},
            "fields": [
                {"name": "ball_x", "address": "0x0", "type": "s16"},
                {"name": "ball_y", "address": "0x2", "type": "s16"},
                {"name": "paddle_y", "address": "0x6", "type": "s16"},
                {"name": "score", "address": "0x8", "type": "u8"},
                {"name": "miss", "address": "0xa", "type": "u8"},
            ],
        }, fh)

    state = GameStateCapability(profiles_dir, "127.0.0.1", ra.port)
    state.attach("pong")
    print("AI is playing Pong using only memory reads (no screenshots)...\n")
    try:
        for i in range(frames):
            s = state.read()["fields"]                 # PERCEIVE (from RAM)
            target = s["ball_y"]                        # DECIDE
            paddle = s["paddle_y"]
            step = max(-6, min(6, target - paddle))     # ACT (here: write paddle;
            state.write_raw("0x6", struct.pack("<h", (paddle + step) & 0xFFFF).hex())  # on device -> input.button)
            if i % 20 == 0:
                print(f"frame {i:3d}  ball=({s['ball_x']:3d},{s['ball_y']:3d})  "
                      f"paddle={paddle:3d}  returns={s['score']}  misses={s['miss']}")
            time.sleep(0.03)
        final = state.read()["fields"]
        print(f"\nResult after {frames} frames: {final['score']} returns, "
              f"{final['miss']} misses — all decided from memory, never a pixel.")
    finally:
        ra.stop()


if __name__ == "__main__":
    main()
