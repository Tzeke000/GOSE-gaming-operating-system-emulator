#!/usr/bin/env python3
# GOSE watchdog (availability/recovery) — keeps the core services alive. If the UI server or the
# Guide-overlay process dies mid-session, restart it. (The kiosk itself is watchdog'd by the ES
# display loop; this covers the pieces that loop doesn't.) Started by gose-session.sh.
import subprocess, time, os, json

INTERVAL = 15
RECENT_F = "/userdata/gose-ui/recent.json"
PLAYTIME_F = "/userdata/gose-ui/playtime.json"
_GAME_RE = "retroarch|emulatorlauncher|ppsspp|pcsx|dolphin-emu|mupen64|duckstation|flycast|mednafen|melonds|scummvm"

def alive(pat):
    return subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0

def game_running():
    return subprocess.run(["pgrep", "-f", _GAME_RE], capture_output=True).returncode == 0

def accrue_playtime():
    # while a game runs, add this interval to the active (most-recently-launched) game's total
    if not game_running():
        return
    try:
        rec = json.load(open(RECENT_F))
        if not rec:
            return
        key = rec[0].get("system", "") + "/" + rec[0].get("game", "")
        try:
            pt = json.load(open(PLAYTIME_F))
        except Exception:
            pt = {}
        pt[key] = pt.get(key, 0) + INTERVAL
        tmp = PLAYTIME_F + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(pt))
        os.replace(tmp, PLAYTIME_F)
    except Exception:
        pass

SERVICES = [
    ("gose_vm_server.py",
     "cd /userdata/gose-ui && nohup python3 -u gose_vm_server.py >>/userdata/gose-ui/server.log 2>&1 &"),
    ("overlay_window.py",
     "cd /userdata/gose-ui && DISPLAY=:0 nohup python3 -u overlay_window.py >>/userdata/gose-ui/overlay.log 2>&1 &"),
]

while True:
    for pat, cmd in SERVICES:
        if not alive(pat):
            try:
                subprocess.run(["/bin/sh", "-c", cmd])
                with open("/userdata/gose-ui/gose.log", "a") as f:
                    f.write("%s WATCHDOG restarted %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), pat))
            except Exception:
                pass
    accrue_playtime()
    time.sleep(INTERVAL)
