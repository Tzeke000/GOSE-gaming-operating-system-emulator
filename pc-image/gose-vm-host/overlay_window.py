#!/usr/bin/env python3
# GOSE Guide overlay — console-style "blurry see-through": on open it screenshots the screen
# (ffmpeg x11grab, works over games/GL too, no compositor needed), blurs it as the backdrop, and
# floats a glass Quick-Access panel on top. Persistent + hidden; SIGUSR1 toggles, SIGUSR2 force-hides.
# Globally triggered by the Openbox KP_5 keybind -> guide_toggle.sh -> pkill -USR1.
import gi, signal, subprocess, os, urllib.request
gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, Gdk, GLib

DARK = Gdk.RGBA(); DARK.parse('#07070f')
BG = "/userdata/gose-ui/_gbg.jpg"

win = Gtk.Window()
win.set_title("GOSE Overlay")
try: win.set_wmclass("gose-overlay", "gose-overlay")
except Exception: pass
win.set_decorated(False)
win.set_skip_taskbar_hint(True)
win.set_skip_pager_hint(True)
try: win.set_type_hint(Gdk.WindowTypeHint.UTILITY)   # don't get auto-fullscreened by the WM rule
except Exception: pass
win.set_keep_above(True)
win.override_background_color(Gtk.StateFlags.NORMAL, DARK)

scr = win.get_screen()
SW = scr.get_width() or 1920
SH = scr.get_height() or 1080
win.set_default_size(SW, SH)

wv = WebKit2.WebView()
try: wv.set_background_color(DARK)
except Exception: pass
st = wv.get_settings()
for p, v in [('enable-webgl', True), ('enable-write-console-messages-to-stdout', True)]:
    try: st.set_property(p, v)
    except Exception: pass
win.add(wv)

state = {"on": False, "busy": False, "n": 0, "last": 0, "game": None}

def active_game_pid():
    # the foreground app, unless it's GOSE itself → that's the game to pause
    try:
        nm = subprocess.run(["/bin/sh", "-c", "DISPLAY=:0 xdotool getactivewindow getwindowname"],
                            capture_output=True, text=True, timeout=3).stdout.strip()
        if nm in ("GOSE", "GOSE Overlay", ""):
            return None
        pid = subprocess.run(["/bin/sh", "-c", "DISPLAY=:0 xdotool getactivewindow getwindowpid"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return int(pid) if pid.isdigit() else None
    except Exception:
        return None

def capture():
    # host-side capture grabs the (now-paused) game frame incl. GL; fall back to guest x11grab
    try:
        with urllib.request.urlopen("http://10.0.2.2:8790/screencap", timeout=10) as r:
            data = r.read()
        if data and len(data) > 2000:
            with open(BG, "wb") as f:
                f.write(data)
            return
    except Exception:
        pass
    try:
        subprocess.run(["/bin/sh", "-c",
            "DISPLAY=:0 ffmpeg -loglevel error -f x11grab -draw_mouse 0 -video_size %dx%d -i :0.0 "
            "-frames:v 1 -y %s" % (SW, SH, BG)], timeout=5)
    except Exception:
        pass

def show():
    state["busy"] = True
    gp = active_game_pid()
    state["game"] = gp
    if gp:
        try: os.kill(gp, signal.SIGSTOP)    # freeze the game while the Guide is up
        except Exception: pass
    capture()
    state["n"] += 1
    # query (not #fragment) so WebKit does a FULL reload each open — picks up the fresh
    # screenshot + any pushed UI changes (a fragment-only change does NOT reload)
    gflag = "&game=1" if state["game"] else ""
    wv.load_uri("http://127.0.0.1:8780/gose-overlay.html?bg=%d%s" % (state["n"], gflag))
    win.show_all(); win.move(0, 0); win.resize(SW, SH)
    win.set_keep_above(True); win.present(); wv.grab_focus()
    state["on"] = True; state["busy"] = False

def hide():
    gp = state.get("game")
    if gp:
        try: os.kill(gp, signal.SIGCONT)    # resume the game
        except Exception: pass
        state["game"] = None
    win.hide(); state["on"] = False

def toggle():
    if state["busy"]:
        return True
    now = GLib.get_monotonic_time()
    if now - state["last"] < 1200000:   # debounce: cursor.js + Openbox can both fire one keypress
        return True
    state["last"] = now
    hide() if state["on"] else show()
    return True

GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, toggle)
GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, lambda *a: (hide(), True)[1])

def on_key(w, e):
    if e.keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace, Gdk.KEY_KP_0,
                    Gdk.KEY_KP_5, Gdk.KEY_KP_Begin):
        hide(); return True
    return False
win.connect('key-press-event', on_key)
win.connect('delete-event', lambda *a: (hide(), True)[1])

win.realize()
Gtk.main()
