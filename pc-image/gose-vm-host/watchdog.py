#!/usr/bin/env python3
# GOSE watchdog (availability/recovery) — keeps the core services alive. If the UI server or the
# Guide-overlay process dies mid-session, restart it. (The kiosk itself is watchdog'd by the ES
# display loop; this covers the pieces that loop doesn't.) Started by gose-session.sh.
#
# Stranger's-hands resilience (gap J1): a boot-success counter detects a crash-looping UI push and
# trips SAFE MODE — auto-restore the last known-good UI (gose-ui.prev) if we have one, else serve a
# minimal static safe-mode page so the device is never a black brick in someone else's hands.
#
# The counter contract:
#   * watchdog INCREMENTS .boot_attempts each time it has to (re)start the UI server.
#   * gose_vm_server CLEARS it (writes 0) the moment it serves the home page == "this boot is good".
#   * a crash-loop never reaches home, so the count climbs; at THRESHOLD the watchdog trips safe mode.
# Everything below is env-parametrized so the safe-mode path can be exercised on a throwaway
# port/dir without touching the live UI.
import subprocess, time, os, json, urllib.request, http.server, socketserver, threading, shutil, sys, socket

INTERVAL   = int(os.environ.get("GOSE_WD_INTERVAL", "15"))
UI_DIR     = os.environ.get("GOSE_WD_UI_DIR", "/userdata/gose-ui")
UI_PORT    = int(os.environ.get("GOSE_WD_UI_PORT", "8780"))
THRESHOLD  = int(os.environ.get("GOSE_WD_THRESHOLD", "3"))
PREV_DIR   = os.environ.get("GOSE_WD_PREV_DIR", "/userdata/gose-ui.prev")
# the command used to (re)start the UI server — overridable so a test can point at a crashing stub
UI_CMD     = os.environ.get(
    "GOSE_WD_UI_CMD",
    "cd %s && nohup python3 -u gose_vm_server.py >>%s/server.log 2>&1 &" % (UI_DIR, UI_DIR))
UI_PAT     = os.environ.get("GOSE_WD_UI_PAT", "gose_vm_server.py")

BOOT_F     = UI_DIR + "/.boot_attempts"
SAFE_F     = UI_DIR + "/.safe_mode"
RECENT_F   = UI_DIR + "/recent.json"
PLAYTIME_F = UI_DIR + "/playtime.json"
_GAME_RE = "retroarch|emulatorlauncher|ppsspp|pcsx|dolphin-emu|mupen64|duckstation|flycast|mednafen|melonds|scummvm"

# ---- singleton guard ---------------------------------------------------------
# Only one watchdog should run at a time. On startup we try to claim a lockfile
# by writing our PID. If the file already exists AND the PID inside it belongs to
# a live process, we exit immediately — the incumbent watchdog is still running.
_LOCK_FILE = os.environ.get("GOSE_WD_LOCK", "/tmp/gose-watchdog.lock")

def acquire_singleton():
    """Claim the singleton lock or exit if another watchdog is already alive.

    Strategy: read the existing pidfile (if any); if the PID it contains is live,
    log and exit.  Otherwise overwrite it with our own PID and continue.
    Uses an atomic write-then-rename so a concurrent second instance that reads
    during our write sees either the old or the new content, never a partial file.
    """
    my_pid = os.getpid()
    # Check for an existing claim.
    try:
        existing = open(_LOCK_FILE).read().strip()
        if existing:
            try:
                other = int(existing)
                if other != my_pid:
                    # Signal 0 = check existence without sending a signal.
                    os.kill(other, 0)
                    # If we reach here the other process is alive — yield.
                    # Write to gose.log best-effort; may not exist yet.
                    try:
                        with open(UI_DIR + "/gose.log", "a") as f:
                            f.write("%s WATCHDOG singleton: PID %d already running; exiting.\n"
                                    % (time.strftime("%Y-%m-%d %H:%M:%S"), other))
                    except Exception:
                        pass
                    sys.exit(0)
            except (ValueError, ProcessLookupError, PermissionError):
                # Stale / unreadable PID — safe to take the lock.
                pass
    except (IOError, OSError):
        pass  # File doesn't exist yet — that's fine.

    # Write our own PID atomically.
    try:
        tmp = _LOCK_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(my_pid))
        os.replace(tmp, _LOCK_FILE)
    except Exception as e:
        # Can't write the lock (read-only FS?). Log and continue — degraded but running.
        try:
            with open(UI_DIR + "/gose.log", "a") as f:
                f.write("%s WATCHDOG warning: could not write lock file %s: %s\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), _LOCK_FILE, e))
        except Exception:
            pass

def release_singleton():
    """Remove the lockfile on clean exit so a fresh watchdog can start immediately."""
    try:
        os.remove(_LOCK_FILE)
    except Exception:
        pass

# ---- port-free helper --------------------------------------------------------
def wait_port_free(port, timeout=10):
    """Kill anything holding *port* and wait up to *timeout* seconds for it to close.

    Used before starting a replacement server so we don't race into an
    'address already in use' bind error.
    """
    # Kill by pattern first (same approach as kill_ui_server).
    try:
        out = subprocess.run(["pgrep", "-f", UI_PAT], capture_output=True, text=True)
        for pid in out.stdout.split():
            try:
                subprocess.run(["kill", pid])
            except Exception:
                pass
    except Exception:
        pass

    # Also ask fuser/ss to find anything else squatting on the port.
    for killer in (
        ["fuser", "-k", "%d/tcp" % port],
        ["fuser", "-k", "%d/tcp6" % port],
    ):
        try:
            subprocess.run(killer, capture_output=True)
        except Exception:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.socket()
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            s.close()
            # Port still in use — wait.
            time.sleep(0.5)
        except (ConnectionRefusedError, OSError):
            return  # Port is free.
    # Timeout — proceed anyway; the server start may fail, watchdog will retry.

def alive(pat):
    return subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0

def game_running():
    return subprocess.run(["pgrep", "-f", _GAME_RE], capture_output=True).returncode == 0

def accrue_playtime():
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

# ---- boot-success counter ---------------------------------------------------
def read_attempts():
    # None == file missing (never started / unknown); int otherwise (0 == explicitly cleared by a good boot)
    try:
        return int(open(BOOT_F).read().strip() or "0")
    except Exception:
        return None

def bump_attempts():
    n = read_attempts() or 0
    n += 1
    try:
        tmp = BOOT_F + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(n))
        os.replace(tmp, BOOT_F)
    except Exception:
        pass
    return n

def clear_attempts():
    try:
        tmp = BOOT_F + ".tmp"
        with open(tmp, "w") as f:
            f.write("0")
        os.replace(tmp, BOOT_F)
    except Exception:
        pass

def server_healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % UI_PORT, timeout=4) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception:
        return False

def log(msg):
    try:
        with open(UI_DIR + "/gose.log", "a") as f:
            f.write("%s WATCHDOG %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass

# ---- known-good snapshot + restore -----------------------------------------
_SNAP_EXCLUDES = ["*.log", "*.log.*", "__pycache__", "*.tmp", ".boot_attempts",
                  ".safe_mode", "_stream_test.bin"]

def snapshot_prev():
    # mirror the current (confirmed-good) UI dir to PREV_DIR as a rollback target. rsync --delete so
    # PREV is an exact mirror minus caches/logs. Best-effort; never let it crash the watchdog.
    try:
        os.makedirs(PREV_DIR, exist_ok=True)
        cmd = ["rsync", "-a", "--delete"]
        for e in _SNAP_EXCLUDES:
            cmd += ["--exclude", e]
        cmd += [UI_DIR.rstrip("/") + "/", PREV_DIR.rstrip("/") + "/"]
        subprocess.run(cmd, capture_output=True, timeout=120)
        log("snapshot -> %s (known-good)" % PREV_DIR)
        return True
    except Exception as e:
        log("snapshot failed: %s" % e)
        return False

def restore_prev():
    # roll the UI dir back to the last known-good snapshot (does NOT touch .boot_attempts here)
    if not (os.path.isdir(PREV_DIR) and os.listdir(PREV_DIR)):
        return False
    try:
        cmd = ["rsync", "-a", "--delete"]
        for e in _SNAP_EXCLUDES:
            cmd += ["--exclude", e]
        cmd += [PREV_DIR.rstrip("/") + "/", UI_DIR.rstrip("/") + "/"]
        subprocess.run(cmd, capture_output=True, timeout=120)
        log("restored previous UI from %s" % PREV_DIR)
        return True
    except Exception as e:
        log("restore_prev failed: %s" % e)
        return False

# ---- safe mode --------------------------------------------------------------
SAFE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GOSE — Safe Mode</title>
<style>*{box-sizing:border-box}html,body{margin:0;height:100%;font-family:system-ui,Segoe UI,sans-serif;
background:radial-gradient(120% 90% at 50% -10%,#1a1f3a,#0c0c1e 55%,#07070f);color:#e8eaf2;
display:flex;align-items:center;justify-content:center}
.card{max-width:560px;padding:34px 38px;background:rgba(20,20,34,.72);border:1px solid #2a2f4a;
border-radius:18px;box-shadow:0 18px 60px #0008}
h1{margin:0 0 6px;font-size:22px}.s{color:#9aa0b8;font-size:13px;margin-bottom:18px}
p{line-height:1.5;color:#c6cadb;font-size:14px}
.btns{display:flex;gap:10px;margin-top:22px;flex-wrap:wrap}
button{font:inherit;font-weight:600;font-size:14px;padding:12px 18px;border-radius:11px;cursor:pointer;
border:2px solid transparent;outline:none}
button:focus{border-color:#7c5cff;box-shadow:0 0 0 3px #7c5cff44}
.primary{background:#7c5cff;color:#fff}.ghost{background:#1c2036;color:#cfd3e6;border-color:#333a57}
.note{margin-top:16px;font-size:12px;color:#7e8398}</style></head>
<body><div class="card">
<h1>GOSE is in Safe Mode</h1>
<div class="s">The interface didn't start cleanly after several tries, so GOSE paused here to stay usable.</div>
<p>Your games, saves and settings are untouched. You can roll back to the last working interface,
or try starting again.</p>
<div class="btns">
<button class="primary" id="restore" onclick="act('/boot/restore')">Restore previous interface</button>
<button class="ghost" id="retry" onclick="act('/boot/retry')">Try starting again</button>
</div>
<div class="note" id="note">Tip: use a controller or the arrow keys + Enter to choose.</div>
</div>
<script>
let i=0;const b=[...document.querySelectorAll('button')];function f(){b[i].focus();}
addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='ArrowDown'||e.key==='Tab'){i=(i+1)%b.length;f();e.preventDefault();}
else if(e.key==='ArrowLeft'||e.key==='ArrowUp'){i=(i-1+b.length)%b.length;f();e.preventDefault();}
else if(e.key==='Enter'||e.key===' '){b[i].click();e.preventDefault();}});f();
let pp={};function pad(){const g=navigator.getGamepads&&[...navigator.getGamepads()].find(x=>x);
if(g){const t=k=>g.buttons[k]&&g.buttons[k].pressed&&!pp[k];
if(t(13)||t(15)){i=(i+1)%b.length;f();}if(t(12)||t(14)){i=(i-1+b.length)%b.length;f();}
if(t(0))b[i].click();g.buttons.forEach((x,k)=>pp[k]=x.pressed);}requestAnimationFrame(pad);}pad();
async function act(u){document.getElementById('note').textContent='Working…';
try{const r=await(await fetch(u,{method:'POST'})).json();
document.getElementById('note').textContent=r.msg||'Done — restarting…';}catch(e){
document.getElementById('note').textContent='Restarting…';}}
</script></body></html>"""

class _SafeHandler(http.server.BaseHTTPRequestHandler):
    server_version = "GOSE-SafeMode/1"
    def log_message(self, *a):
        pass
    def _send(self, code, ctype, body):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health":
            return self._send(200, "application/json", json.dumps({"ok": True, "safe_mode": True}))
        return self._send(200, "text/html; charset=utf-8", SAFE_HTML)
    def do_POST(self):
        action = self.path.split("?")[0]
        if action == "/boot/restore":
            ok = restore_prev()
            msg = "Restored — restarting the interface…" if ok else "No previous interface saved; trying again…"
            clear_attempts()
            self._send(200, "application/json", json.dumps({"ok": ok, "msg": msg}))
            self.server._exit = True
        elif action == "/boot/retry":
            clear_attempts()
            self._send(200, "application/json", json.dumps({"ok": True, "msg": "Restarting the interface…"}))
            self.server._exit = True
        else:
            self._send(404, "application/json", json.dumps({"ok": False}))

class _SafeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    _exit = False

def kill_ui_server():
    # free the UI port before binding the safe server (the crashing server may be mid-restart)
    try:
        out = subprocess.run(["pgrep", "-f", UI_PAT], capture_output=True, text=True)
        for pid in out.stdout.split():
            try:
                subprocess.run(["kill", pid])
            except Exception:
                pass
    except Exception:
        pass

_restored_once = False   # auto-rollback is tried at most once, then we park on the static page

def enter_safe_mode():
    global _restored_once
    log("SAFE MODE tripped (>= %d failed UI starts)" % THRESHOLD)
    try:
        open(SAFE_F, "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    # Preferred recovery: silently roll back to the last known-good UI ONCE and let the loop restart it.
    # If a rollback already happened and we tripped again, don't loop — park on the static page below.
    if not _restored_once and os.path.isdir(PREV_DIR) and os.listdir(PREV_DIR):
        if restore_prev():
            _restored_once = True
            clear_attempts()
            try: os.remove(SAFE_F)
            except Exception: pass
            log("auto-restored previous UI; resuming normal operation")
            return
    # No rollback target: hold here serving the static safe-mode page until a human chooses.
    kill_ui_server()
    time.sleep(1)
    try:
        srv = _SafeServer(("127.0.0.1", UI_PORT), _SafeHandler)
    except Exception as e:
        log("could not bind safe-mode server on %d: %s" % (UI_PORT, e))
        clear_attempts()   # avoid a tight trip-loop if we can't bind
        return
    log("serving static safe-mode page on 127.0.0.1:%d" % UI_PORT)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    while not srv._exit:
        time.sleep(0.5)
    srv.shutdown()
    try: os.remove(SAFE_F)
    except Exception: pass
    log("left safe mode (human action); resuming normal operation")

SERVICES = [
    ("overlay_window.py",
     "cd %s && DISPLAY=:0 nohup python3 -u overlay_window.py >>%s/overlay.log 2>&1 &" % (UI_DIR, UI_DIR)),
]

def main():
    # Singleton guard: exit immediately if another watchdog instance is alive.
    acquire_singleton()
    log("started (PID %d)" % os.getpid())

    snapped = False
    try:
        while True:
            # 1) UI server: restart if down, counting each (re)start as a boot attempt
            if not alive(UI_PAT):
                # Ensure the old server process and its port are fully gone before we
                # start a replacement.  Without this, a just-exited server can hold the
                # port for a moment, causing the fresh start to fail with EADDRINUSE and
                # inflate the boot-attempts counter spuriously.
                wait_port_free(UI_PORT)
                n = bump_attempts()
                try:
                    subprocess.run(["/bin/sh", "-c", UI_CMD])
                    log("restarted UI server (attempt %d)" % n)
                except Exception:
                    pass
                snapped = False   # new (re)start = a new boot streak; allow a fresh snapshot once it's good

            # 2) other services (Guide overlay) — unchanged behavior
            for pat, cmd in SERVICES:
                if not alive(pat):
                    try:
                        subprocess.run(["/bin/sh", "-c", cmd])
                        log("restarted %s" % pat)
                    except Exception:
                        pass

            # 3) confirmed-good boot? snapshot a rollback target (once per streak)
            att = read_attempts()
            if att == 0 and not snapped and server_healthy():
                snapshot_prev()
                snapped = True

            # 4) crash-loop? trip safe mode
            if (att or 0) >= THRESHOLD:
                enter_safe_mode()

            accrue_playtime()
            time.sleep(INTERVAL)
    finally:
        release_singleton()

if __name__ == "__main__":
    main()
