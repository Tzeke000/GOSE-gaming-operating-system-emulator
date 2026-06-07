#!/usr/bin/env python3
"""GOSE gamepad -> keyboard bridge.

WebKit2GTK in the GOSE kiosk has no gamepad library (libmanette absent) and no
gamepad->key mapper (evmapy/gptokeyb absent), so the controller cannot drive the
controller-first UI on its own. BUT the UI's navigation is keyboard-based and
verified working (arrows move focus, [ / ] switch store tabs, Enter activates,
Esc -> desktop). This daemon reads the controller via python-evdev and synthesizes
the matching X input events -- making the whole UI controller-driven.

PAUSE rule: when a game/emulator (retroarch / emulatorlauncher) is foreground, the
pad belongs to the game (RetroArch reads evdev directly); we must NOT emit phantom
keys. We detect that and go silent until the game exits.

Injection engine: a PERSISTENT X connection doing XTEST fake events (python-xlib,
vendored in ./vendor) -- sub-ms per key vs ~50-100ms per xdotool spawn. If the
Xlib import/X connect fails we fall back to spawning `DISPLAY=:0 xdotool` per
event (the original PROVEN path; X runs without -auth so no XAUTHORITY needed).
NOTE: XSendEvent is IGNORED by WebKit -- XTEST (like `xdotool key` without
--window) is the path that works.

INPUT MODEL (docs/27 §1): the d-pad moves FOCUS (arrow keysyms); the LEFT STICK
moves a real X POINTER (XTEST relative motion, >=60Hz while deflected, linear
accel up to CURSOR_MAX_SPEED). A clicks at the pointer when the cursor is active
(moved within CURSOR_CLICK_WINDOW); otherwise A stays Enter for focus-nav. The
cursor auto-hides after CURSOR_HIDE_S idle (XFixes hide/show) and WAKES on any
pointer activity: stick motion, EXTERNAL pointer motion the bridge didn't make
(a real host mouse/tablet -- one QueryPointer per bridge tick against a baseline
that excludes our own XTEST moves), or a pressed pointer button. Cursor motion
AND the wake obey the same game-suppression as keys.

Config (device match + keymaps) is in editable dicts at the top of this file.
The mapping is pure/unit-testable via PadNav.map_event (no X required) -- see
the __main__ self-test (`gose-pad-nav.py --selftest`).
"""
import os
import sys
import time
import glob
import json
import struct
import select
import subprocess
import urllib.request

import evdev
from evdev import ecodes

# Vendored pure-python deps (python-xlib + six) live beside this file; first on
# sys.path so the XTEST engine works on the stock guest image (no pip there).
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

# ---------------------------------------------------------------------------
# CONFIG  (edit here)
# ---------------------------------------------------------------------------

# A device drives nav ONLY if it exposes real PAD BUTTONS (EV_KEY codes in the
# joystick/gamepad/d-pad ranges). Name keywords are NOT sufficient: a composite
# pad ships sibling nodes — "<pad name> Motion Sensors", "... Touchpad",
# "... Headset Jack" — whose names also say "Controller" but which can only
# produce noise. The DualSense motion node streams ~1.4k ev/s constantly and
# its ACCELEROMETER crosses the stick deadzone whenever the pad is physically
# handled, emitting phantom arrow keys; each phantom spawns an xdotool process
# (~50-100ms, serialized), so real presses queued seconds behind garbage — the
# "7.2s d-pad lag" of 2026-06-07. Universal rule, no per-pad special cases:
#   BTN_MISC..BTN_MOUSE   (0x100-0x10f)  exotic DInput pads
#   BTN_JOYSTICK..BTN_DIGI(0x120-0x13f)  classic joystick + gamepad (BTN_SOUTH..)
#   BTN_DPAD_*            (0x220-0x223)  discrete d-pad buttons
# Mouse buttons (0x110-0x117, touchpads) and digitizer codes (0x140-0x14f,
# BTN_TOUCH/BTN_TOOL_FINGER) are deliberately OUTSIDE these ranges, as are
# plain keyboard keys (< 0x100) and switch-only nodes (headset jacks).
GAMEPAD_BTN_RANGES = (
    (ecodes.BTN_MISC, ecodes.BTN_MOUSE),          # 0x100-0x10f
    (ecodes.BTN_JOYSTICK, ecodes.BTN_DIGI),       # 0x120-0x13f
    (ecodes.BTN_DPAD_UP, ecodes.BTN_DPAD_RIGHT + 1),  # 0x220-0x223
)

# Button (EV_KEY) -> X keysym.  Only key-DOWN (value==1) fires; autorepeat
# (value==2) and release (value==0) are ignored -> debounces button mash.
BUTTON_KEYMAP = {
    ecodes.BTN_SOUTH:  "Return",        # A
    ecodes.BTN_EAST:   "Escape",        # B
    ecodes.BTN_TL:     "bracketleft",   # L1  -> prev store tab
    ecodes.BTN_TR:     "bracketright",  # R1  -> next store tab
    ecodes.BTN_START:  "Return",        # Start (also activates)
    ecodes.BTN_SELECT: "Escape",        # Select (optional back)
    # Discrete d-pad buttons (BTN_DPAD_*): only ever reported by pads that expose
    # the d-pad as buttons instead of ABS_HAT0 (some generic/DInput pads, and the
    # target the DB normalizer remaps a button-d-pad to). The virtual pad + every
    # kernel-driver pad (Xbox/DS4/DualSense/Switch/8BitDo) use ABS_HAT0 instead, so
    # these are inert for them -> pure addition, zero regression.
    ecodes.BTN_DPAD_UP:    "Up",
    ecodes.BTN_DPAD_DOWN:  "Down",
    ecodes.BTN_DPAD_LEFT:  "Left",
    ecodes.BTN_DPAD_RIGHT: "Right",
}

# Axis (EV_ABS) -> directional keysyms.  Hats are ternary (-1 / 0 / +1).
# Held directions auto-repeat.  The LEFT STICK (ABS_X/Y) is deliberately NOT
# here (docs/27 model change 2026-06-07): d-pad = focus-nav keys, left stick =
# the pointer CURSOR (see the CURSOR_* constants + PadNav cursor handling).
AXIS_MAP = {
    ecodes.ABS_HAT0X: {"neg": "Left", "pos": "Right"},
    ecodes.ABS_HAT0Y: {"neg": "Up",   "pos": "Down"},
}

# Left-stick axes -> pointer motion (NOT keysyms).
CURSOR_AXES = (ecodes.ABS_X, ecodes.ABS_Y)

STICK_DEADZONE = 12000   # |value| past this (from center 0) = deflected
STICK_FULL = 32767       # nominal full deflection of an Xbox-style stick
REPEAT_INITIAL = 0.40    # s before a held direction starts repeating
REPEAT_INTERVAL = 0.18   # s between repeats while held (~180ms)
GAME_CACHE_S = 0.30      # cache the "is a game running" check this long
DEVICE_RESCAN_S = 2.0    # poll /dev/input for hotplug this often

# --- stick cursor (docs/27 §1.1) --------------------------------------------
CURSOR_MAX_SPEED = 900.0     # px/s at full deflection (linear with deflection)
CURSOR_TICK_S = 0.012        # motion update period while deflected.  Nominal
                             # target is >=60Hz; the guest's select() carries
                             # ~1.5ms timer slack (16ms waits measured 17.5ms,
                             # 57/s bare), so 12ms is what actually DELIVERS
                             # >=60 updates/s in the VM.  Speed is dt-scaled,
                             # so the tick rate never changes cursor speed.
CURSOR_CLICK_WINDOW = 1.5    # s since last motion during which A = click
CURSOR_HIDE_S = 5.0          # s of pointer idle before the X cursor auto-hides

LOGFILE = "/userdata/system/logs/gose-pad-nav.log"

# Process names that mean "a game/emulator owns the pad -> stay silent".
GAME_PGREP_EXACT = ("retroarch",)            # pgrep -x
GAME_PGREP_FULL = ("emulatorlauncher",)      # pgrep -f

# --- OS-control arbitration (admin gating) ---------------------------------
# Only the OS-admin / Player-1 controller (plus the dev pad + an admin-tier AI's
# seat) may drive the OS menus.  A friend's pad does nothing in menus but still
# works in games (games read evdev directly; this daemon is silent then anyway).
SERVER_URL = "http://127.0.0.1:8780"             # in-guest GOSE server (controller registry)
OS_ADMIN_FILE = "/userdata/system/gose/os_admin_controller.json"  # admin-id fallback source
OOBE_DONE_FILE = "/userdata/system/gose/.oobe-done"  # absent = first-boot wizard not finished yet
AI_TOKENS_FILE = os.environ.get("GOSE_AGENT_AI_TOKENS",
                                "/userdata/system/gose/ai_tokens.json")  # token->{name,tier[,seat]}
GATE_REFRESH_S = 3.0                              # re-read registry/admin/tokens this often

# Game Bar overlay drops this file while it is open.  When present we do NOT
# suppress emits even though a game process is running, so the pad can drive the
# bar.  Removing the file restores the normal "game owns the pad" silence.
GAMEBAR_FLAG = "/tmp/gose-gamebar-open"

# --- WM modal layer (docs/23 §7, chunk B) -----------------------------------
# Guide (BTN_MODE) held -> the window carousel; L2 + d-pad -> the Snap chooser.
# While a WM modal is active the bridge POSTs SEMANTIC events to /wm/event
# (wm.next/prev/select/cancel/...) instead of synthesizing keys; the shell's
# long-poll picks them up in milliseconds.  The flag file mirrors the Game-Bar
# exception so the WM layer keeps working over a native app later (phase 2).
WM_FLAG = "/tmp/gose-wm-open"
WM_EVENT_URL = SERVER_URL + "/wm/event"
GUIDE_HOLD_S = 0.35      # Guide released after this long = "release selects"
L2_THRESHOLD = 100       # ABS_Z (0-255) past this counts as L2 held
# keysym -> semantic event while a WM modal is open (reuses map_event's output)
WM_FROM_KEYSYM = {
    "Left": "wm.left", "Right": "wm.right", "Up": "wm.up", "Down": "wm.down",
    "Return": "wm.select", "Escape": "wm.cancel",
    "bracketleft": "wm.prev", "bracketright": "wm.next",
}

# --- native-app discipline (docs/27 §4.1, docs/23 app-class policy) ----------
# A launched native X app (Firefox/VLC/Chromium/...) is a sibling window OVER the
# kiosk; the pad must never trap there ("the browser-trap").  While a native app
# is FOREGROUND and NO game is running:
#   B     -> politely close that window (EWMH _NET_CLOSE_WINDOW ClientMessage —
#            the wmctrl-equivalent; an XTEST Escape would go INTO the app)
#   Guide -> re-activate the kiosk window (escape hatch) + the WM layer posts
#            wm.carousel as usual, so the user lands in the switcher
#   everything else (keys, stick cursor, A-clicks) flows to the app unchanged.
# Foreground state comes from a BACKGROUND poll (_NET_ACTIVE_WINDOW + WM_CLASS)
# on the GameWatch cadence — never the per-event hot path.  New native windows
# are maximized once on first foreground sighting (the app-class policy: native
# apps open fullscreen-maximized), skipped while a game runs.
NATIVE_POLL_S = 0.5          # foreground re-check cadence (background thread)
KIOSK_WM_CLASSES = ("kiosk.py", "gose-overlay")   # verified live 2026-06-07
KIOSK_WM_NAMES = ("GOSE", "GOSE Overlay")

# --- UNIVERSAL controller normalization (SDL_GameControllerDB) ---------------
# "One button language" for ANY pad — the software 8BitDo-dongle. We DON'T write
# per-controller mappings; we adopt the community SDL_GameControllerDB
# (gabomdq/SDL_GameControllerDB, `gamecontrollerdb.txt`, vendored beside this file
# and deployed to /userdata/gose-ui). Each connected pad is identified by its SDL
# GUID (computed from the evdev bus/vendor/product/version); the DB row says which
# standard button (a/b/x/y/dpad/L1/start/guide/...) each of that pad's physical
# inputs is. The Normalizer turns every pad's RAW evdev codes into the STANDARD
# evdev codes the rest of this bridge already speaks (BTN_SOUTH, ABS_HAT0X, ...),
# so map_event/WMLayer/AdminGate are untouched and a DualSense's Cross == Xbox A
# == Enter without any controller-specific code.
#
# Linux kernel HID drivers (xpadneo/hid-playstation/hid-nintendo/native 8BitDo)
# already report position-standard evdev codes (bottom face button == BTN_SOUTH,
# d-pad == ABS_HAT0). For those pads (which is ALL of Xbox/PS4/PS5/Switch/8BitDo)
# the normalizer is the IDENTITY — the proven path is unchanged (zero regression,
# incl. the virtual Xbox-360 test pad). The DB-driven remap only engages for a pad
# whose evdev codes are NON-standard (no BTN_SOUTH) yet known to the DB, e.g. a
# cheap generic/DInput pad. Unknown pads fall back to the sane evdev defaults.
GCDB_PATHS = (
    "/userdata/gose-ui/gamecontrollerdb.txt",          # our deployed copy (canonical)
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "gamecontrollerdb.txt"),
    "/usr/share/sdl-jstest/gamecontrollerdb.txt",       # image fallbacks
    "/usr/share/ppsspp/PPSSPP/gamecontrollerdb.txt",
)

# Standard SDL button/axis name -> the STANDARD evdev code the bridge expects.
STD_BTN_TO_EVDEV = {
    "a": ecodes.BTN_SOUTH, "b": ecodes.BTN_EAST,
    "x": ecodes.BTN_WEST,  "y": ecodes.BTN_NORTH,
    "leftshoulder": ecodes.BTN_TL, "rightshoulder": ecodes.BTN_TR,
    "lefttrigger": ecodes.BTN_TL2, "righttrigger": ecodes.BTN_TR2,
    "start": ecodes.BTN_START, "back": ecodes.BTN_SELECT,
    "guide": ecodes.BTN_MODE,
    "leftstick": ecodes.BTN_THUMBL, "rightstick": ecodes.BTN_THUMBR,
    "dpup": ecodes.BTN_DPAD_UP, "dpdown": ecodes.BTN_DPAD_DOWN,
    "dpleft": ecodes.BTN_DPAD_LEFT, "dpright": ecodes.BTN_DPAD_RIGHT,
}
STD_AXIS_TO_EVDEV = {
    "leftx": ecodes.ABS_X, "lefty": ecodes.ABS_Y,
    "rightx": ecodes.ABS_RX, "righty": ecodes.ABS_RY,
    "lefttrigger": ecodes.ABS_Z, "righttrigger": ecodes.ABS_RZ,
}


# ---------------------------------------------------------------------------
def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # Only echo to stdout when interactive (selftest). As a daemon, stdout is
    # redirected back into LOGFILE -> printing here would double every line.
    if sys.stdout.isatty():
        print(line, flush=True)


def _game_pids():
    """PIDs of running game/emulator processes.  NOTE this is EXPENSIVE on the
    guest (pgrep spawn ~tens of ms; a pure-python /proc scan measured ~0.7s for
    418 pids and would hold the GIL) — it must only ever run inside GameWatch's
    background thread, NEVER on the 60Hz cursor/select path (that's what capped
    the cursor at ~25Hz on 2026-06-07).  pgrep is preferred over a python scan
    here precisely because the work happens in a separate PROCESS: the bridge's
    GIL stays free while the thread blocks on it."""
    pids = []
    for name in GAME_PGREP_EXACT:
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
        pids += [p for p in out.stdout.split() if p]
    for name in GAME_PGREP_FULL:
        out = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True)
        pids += [p for p in out.stdout.split() if p]
    return pids


def _pid_state(pid):
    """The single-char process state from /proc/<pid>/stat (R/S/D/T/t/Z...)."""
    try:
        with open("/proc/%s/stat" % pid) as fh:
            data = fh.read()
        return data[data.rindex(")") + 2]   # field right after comm's ')'
    except Exception:
        return ""


def default_game_running():
    """True if a game/emulator is foreground AND ACTIVE (not SIGSTOPped).
    When the Game Bar opens it SIGSTOPs the game (state 'T') but the process
    lingers, so a plain pgrep would wrongly keep the pad away from the bar.
    A stopped game can't read the pad -> it no longer 'owns' it -> the bridge
    should resume so the controller drives the bar. On bar-close SIGCONT
    restores R/S and this re-pauses. (1b's robust insight; flag stays as belt-and-suspenders.)"""
    pids = _game_pids()
    if not pids:
        return False
    for pid in pids:
        st = _pid_state(pid)
        if st and st not in ("T", "t"):
            return True
    return False


def default_gamebar_open():
    """True if the Game Bar overlay is open (its flag file exists)."""
    return os.path.exists(GAMEBAR_FLAG)


class GameWatch:
    """Keeps the 'is a game running' answer fresh OFF the hot path.

    The select()/tick() loop runs at >=60Hz while the stick cursor is moving;
    it must never spawn pgrep or scan /proc (both measured slow enough on the
    guest to cap the cursor at ~25Hz).  This watcher refreshes the answer in a
    daemon thread every `interval`, and is_running() is a plain attribute read.
    The thread uses pgrep (see _game_pids): the scan work happens in a child
    process, so the bridge's GIL stays free."""

    def __init__(self, check=None, interval=GAME_CACHE_S):
        self._check = check or default_game_running
        self.interval = interval
        self.value = False

    def refresh(self):
        try:
            self.value = bool(self._check())
        except Exception:
            self.value = False
        return self.value

    def start(self):
        self.refresh()                 # honest initial state before the thread
        def _loop():
            while True:
                time.sleep(self.interval)
                self.refresh()
        _threading.Thread(target=_loop, daemon=True).start()
        return self

    def is_running(self):
        return self.value


def default_wm_open():
    """True if the WM modal layer is active (its flag file exists)."""
    return os.path.exists(WM_FLAG)


# ---- /wm/event poster: a single worker thread so posts never block the evdev
# loop AND arrive in order (left,left,select must not reorder). ----------------
import queue as _queue
import threading as _threading
_WM_POST_Q = _queue.Queue(maxsize=64)

def _wm_post_worker():
    while True:
        ev = _WM_POST_Q.get()
        try:
            req = urllib.request.Request(
                WM_EVENT_URL, data=json.dumps({"event": ev}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=1.5).read()
        except Exception as e:
            log("wm post %s FAILED: %s" % (ev, e))

def wm_post(ev):
    try:
        _WM_POST_Q.put_nowait(ev)
        log("wm event %s" % ev)
    except _queue.Full:
        log("wm post queue full -> dropped %s" % ev)


class WMLayer:
    """The modal window-management layer (docs/23 §7).

    States:  None (normal nav) | "carousel" (Guide) | "snap" (L2+d-pad).
    * Guide DOWN  -> enter carousel, drop WM_FLAG, post wm.carousel.
    * Guide UP    -> held >= GUIDE_HOLD_S: post wm.select + exit (release-selects);
                     shorter: STICKY — the modal stays, A/B finish it.
    * In a modal every mapped keysym becomes a semantic event (WM_FROM_KEYSYM);
      Y -> wm.overview, X -> wm.act.  Nothing is synthesized as a key.
    * L2 held + d-pad (outside a modal) -> enter snap, post wm.snapmode; further
      d-pad posts directions; A places (stays for Snap-Assist); L2 release exits
      the bridge modal (the page may keep its assist UI; keys then drive it).
    * Admin-gated: the same gate as menu nav decides who may enter the layer.
    The flag file mirrors the Game-Bar exception (is_suppressed honours it), so
    the layer still works while a game/native app runs.
    """

    def __init__(self, post=None, flag_path=WM_FLAG, gate=None):
        self.post = post or wm_post
        self.flag_path = flag_path
        self.gate = gate or (lambda path, name=None: (True, "no-gate"))
        self.mode = None          # None | "carousel" | "snap"
        self.guide_t = 0.0
        self.l2 = {}              # dev_path -> bool (per-device trigger state)

    # -- flag lifecycle ------------------------------------------------------
    def _enter(self, mode):
        self.mode = mode
        try:
            open(self.flag_path, "w").close()
        except Exception:
            pass

    def _exit(self):
        self.mode = None
        try:
            os.remove(self.flag_path)
        except Exception:
            pass

    def _allowed(self, dev_path, dev_name):
        try:
            ok, why = self.gate(dev_path, dev_name)
        except Exception:
            ok, why = True, "gate error -> fail-open"
        if not ok:
            log("wm layer denied: %s %s" % (dev_name or dev_path or "?", why))
        return ok

    # -- the handler: returns True when the event was consumed by the layer ---
    def handle(self, event, dev_path, dev_name, keysyms):
        et, ec, val = event.type, event.code, event.value

        # L2 tracking (analog ABS_Z or digital BTN_TL2); release exits snap mode
        if et == ecodes.EV_ABS and ec == ecodes.ABS_Z:
            held = val >= L2_THRESHOLD
            if self.l2.get(dev_path) != held:
                self.l2[dev_path] = held
                if not held and self.mode == "snap":
                    self._exit()
            return True                       # ABS_Z never maps to nav keys anyway
        if et == ecodes.EV_KEY and ec == ecodes.BTN_TL2:
            self.l2[dev_path] = (val == 1)
            if val == 0 and self.mode == "snap":
                self._exit()
            return True

        # Guide button = the system key (SteamOS convention)
        if et == ecodes.EV_KEY and ec == ecodes.BTN_MODE:
            if val == 1:
                if not self._allowed(dev_path, dev_name):
                    return True
                self.guide_t = time.time()
                self._enter("carousel")
                self.post("wm.carousel")
            elif val == 0 and self.mode == "carousel":
                if time.time() - self.guide_t >= GUIDE_HOLD_S:
                    self.post("wm.select")    # release-selects (the headline gesture)
                    self._exit()
                # else: quick tap -> sticky modal; A/B finish it
            return True

        # WM-only buttons while a modal is open (Y=overview, X=act-out)
        if self.mode and et == ecodes.EV_KEY and val == 1 and \
                ec in (ecodes.BTN_NORTH, ecodes.BTN_WEST):
            self.post("wm.overview" if ec == ecodes.BTN_NORTH else "wm.act")
            return True

        # snap entry: L2 + d-pad/stick direction outside a modal
        if not self.mode and self.l2.get(dev_path) and keysyms and \
                keysyms[0] in ("Left", "Right", "Up", "Down"):
            if not self._allowed(dev_path, dev_name):
                return True
            self._enter("snap")
            self.post("wm.snapmode")
            return True

        # inside a modal every mapped keysym becomes a semantic event
        if self.mode and keysyms:
            for k in keysyms:
                ev = WM_FROM_KEYSYM.get(k)
                if not ev:
                    continue
                self.post(ev)
                if ev == "wm.select" and self.mode == "carousel":
                    self._exit()
                elif ev == "wm.cancel":
                    self._exit()
            return True
        if self.mode and et == ecodes.EV_KEY:
            return True                       # swallow everything else while modal

        return False


def xdotool_emit(keysym):
    env = dict(os.environ, DISPLAY=":0")
    subprocess.call(["xdotool", "key", keysym], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# INPUT ENGINES  (key synthesis + pointer)
# ---------------------------------------------------------------------------
class XTestEngine:
    """Persistent-X-connection input engine: XTEST fake events via the vendored
    pure-python python-xlib.  Sub-ms per key vs ~50-100ms per xdotool spawn
    (the spawn cost was the amplifier of the 2026-06-07 "7.2s d-pad lag").

    XTEST is the ONLY synthetic path WebKit honours: XSendEvent-style events
    carry the synthetic flag and are ignored by it, while XTEST events come out
    of the server like real device input (same reason `xdotool key` without
    --window works).  Pointer motion is XTEST relative MotionNotify (detail=1);
    clicks are XTEST button 1.  Cursor visibility uses the XFixes extension
    (hide/show is refcounted per-client and auto-undone if we disconnect).
    """
    name = "xtest"

    def __init__(self, display=":0"):
        from Xlib import display as _xdisplay, X as _X, XK as _XK
        from Xlib.ext import xtest as _xtest  # noqa: ensures the ext module loads
        self._X = _X
        self._XK = _XK
        self._display_name = display
        self._kc = {}                  # keysym name -> keycode cache
        self.can_hide = False
        # pointer-button bits ONLY (Button1..5 = 0x100..0x1000).  The low bits
        # of QueryPointer's mask are MODIFIERS — NumLock (Mod2, 0x10) is on in
        # the guest, so masking anything below 0x100 would read as a held
        # button forever and pin the cursor visible.
        self._btn_mask = (_X.Button1Mask | _X.Button2Mask | _X.Button3Mask |
                          _X.Button4Mask | _X.Button5Mask)
        self._connect()

    def _connect(self):
        from Xlib import display as _xdisplay
        self.d = _xdisplay.Display(self._display_name)
        if not getattr(self.d, "xtest_fake_input", None):
            raise RuntimeError("X server has no XTEST extension")
        self.root = self.d.screen().root
        self._kc.clear()
        # Fresh connection => the server-side XFixes hide refcount FOR THIS
        # CLIENT is 0 (hides are per-client and auto-undone at disconnect), so
        # a reconnect can never inherit a hidden cursor it can't show again.
        self._hidden = False
        # XFixes (cursor hide/show) is optional: probe honestly, never assume.
        self.can_hide = False
        try:
            if getattr(self.root, "xfixes_hide_cursor", None):
                self.d.xfixes_query_version()   # protocol requires negotiation
                self.can_hide = True
        except Exception:
            self.can_hide = False

    def _retry(self, op):
        """Run op(); on a dead X connection reconnect ONCE and retry."""
        try:
            return op()
        except Exception:
            self._connect()            # may raise -> caller's except handles it
            return op()

    def _keycode(self, keysym_name):
        kc = self._kc.get(keysym_name)
        if kc is None:
            ks = self._XK.string_to_keysym(keysym_name)
            kc = self.d.keysym_to_keycode(ks)
            self._kc[keysym_name] = kc
        return kc

    def key(self, keysym):
        def op():
            kc = self._keycode(keysym)
            if not kc:
                raise RuntimeError("no keycode for %s" % keysym)
            self.d.xtest_fake_input(self._X.KeyPress, kc)
            self.d.xtest_fake_input(self._X.KeyRelease, kc)
            self.d.flush()
        try:
            self._retry(op)
        except Exception as e:
            log("xtest key %s failed (%s) -> xdotool one-shot" % (keysym, e))
            xdotool_emit(keysym)       # never let a nav press vanish

    def move(self, dx, dy):
        def op():
            # detail=1 == RELATIVE motion (XTEST spec); server clamps at edges
            self.d.xtest_fake_input(self._X.MotionNotify, 1, x=dx, y=dy)
            self.d.flush()
        self._retry(op)

    def click(self, button=1):
        def op():
            self.d.xtest_fake_input(self._X.ButtonPress, button)
            self.d.xtest_fake_input(self._X.ButtonRelease, button)
            self.d.flush()
        self._retry(op)

    def pointer_pos(self):
        def op():
            q = self.root.query_pointer()
            return (q.root_x, q.root_y)
        return self._retry(op)

    def pointer_state(self):
        """((x, y), any-pointer-button-down) in ONE QueryPointer round-trip
        (measured 0.77 ms/call on the guest, 2026-06-07).  Used by the
        external-wake check on the existing tick cadence — and skipped
        entirely on the 60Hz self-motion path, so it adds zero hot-path cost."""
        def op():
            q = self.root.query_pointer()
            return ((q.root_x, q.root_y), bool(q.mask & self._btn_mask))
        return self._retry(op)

    def hide_cursor(self):
        """Idempotent PER CONNECTION: XFixes hide is refcounted, so a re-issued
        hide would stack the count and eat a later show — the 'permanently
        invisible' failure class.  _hidden gates it to at most one outstanding
        hide; a reconnect inside _retry resets _hidden because the dead
        client's hide died with it (server auto-reveals)."""
        if not self.can_hide:
            return
        def op():
            if not self._hidden:
                self.root.xfixes_hide_cursor()
                self.d.flush()
                self._hidden = True
        self._retry(op)

    def show_cursor(self):
        if not self.can_hide:
            return
        def op():
            if self._hidden:           # nothing to undo on a fresh connection
                self.root.xfixes_show_cursor()
                self.d.flush()
                self._hidden = False
        self._retry(op)


class XdotoolEngine:
    """Fallback engine: one xdotool spawn per event (the original proven path).
    Slow (~50-100ms/event) -- the cursor will not reach 60Hz on this engine and
    XFixes hide/show is unavailable (parking the pointer as a fake 'hide' is
    FORBIDDEN: it moves the pointer and fires hover side-effects)."""
    name = "xdotool"
    can_hide = False

    @staticmethod
    def _run(*args):
        env = dict(os.environ, DISPLAY=":0")
        subprocess.call(["xdotool"] + list(args), env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def key(self, keysym):
        self._run("key", keysym)

    def move(self, dx, dy):
        self._run("mousemove_relative", "--", str(dx), str(dy))

    def click(self, button=1):
        self._run("click", str(button))

    def pointer_pos(self):
        env = dict(os.environ, DISPLAY=":0")
        try:
            out = subprocess.run(["xdotool", "getmouselocation"], env=env,
                                 capture_output=True, text=True).stdout
            parts = dict(p.split(":", 1) for p in out.split() if ":" in p)
            return (int(parts["x"]), int(parts["y"]))
        except Exception:
            return (0, 0)

    def pointer_state(self):
        # xdotool reports no button state; honest False.  Moot anyway: with
        # can_hide=False the cursor is never hidden, so the wake check skips.
        return (self.pointer_pos(), False)

    def hide_cursor(self):
        pass                            # honest no-op (limitation logged once)

    def show_cursor(self):
        pass


def make_engine():
    """XTEST engine when the vendored Xlib can import + connect; else xdotool.
    Logs which engine is active (the startup line to grep for)."""
    try:
        eng = XTestEngine(":0")
        log("input engine: XTEST (persistent X conn, python-xlib vendored; "
            "xfixes cursor hide=%s)" % ("yes" if eng.can_hide else "NO"))
        return eng
    except Exception as e:
        log("input engine: xdotool FALLBACK (XTEST unavailable: %s)" % e)
        return XdotoolEngine()


# ---------------------------------------------------------------------------
class NativeWatch:
    """Foreground native-X-window watcher + EWMH actions (docs/27 §4.1).

    A daemon thread polls _NET_ACTIVE_WINDOW / WM_CLASS every NATIVE_POLL_S on
    its OWN X connection (the XTEST engine's connection stays single-threaded);
    is_native_fg() is a plain attribute read for the hot path.  Actions:
      close_fg()        — _NET_CLOSE_WINDOW ClientMessage to the foreground app
                          (the polite, EWMH-correct close; XTEST is wrong here)
      activate_kiosk()  — _NET_ACTIVE_WINDOW ClientMessage raising the kiosk
    All X calls share one lock (python-xlib Displays are not thread-safe).
    New native windows are maximized ONCE on first foreground sighting (app-class
    policy: native apps open fullscreen-maximized) unless a game is running.
    classify() is pure -> unit-testable without X.
    """

    def __init__(self, display=":0", game_check=None):
        from Xlib import display as _xdisplay, X as _X, protocol as _proto
        self._X = _X
        self._proto = _proto
        self.d = _xdisplay.Display(display)
        self.root = self.d.screen().root
        self._lock = _threading.Lock()
        self.game_check = game_check or (lambda: False)
        self._atom = {}
        for n in ("_NET_ACTIVE_WINDOW", "_NET_CLIENT_LIST", "_NET_CLOSE_WINDOW",
                  "_NET_WM_STATE", "_NET_WM_STATE_MAXIMIZED_VERT",
                  "_NET_WM_STATE_MAXIMIZED_HORZ", "_NET_WM_NAME", "UTF8_STRING"):
            self._atom[n] = self.d.intern_atom(n)
        self.fg_xid = 0
        self.fg_native = False
        self._fg_known = True        # ident actually read? unknown re-probes
        self.fg_label = ""           # "class/name" for logs
        self._seen = set()           # xids already offered the maximize-once

    # -- pure classification (no X) -------------------------------------------
    @staticmethod
    def classify(wm_class, name):
        """True when the window is the GOSE shell family (kiosk / overlay) —
        i.e. NOT a native app.  wm_class is the (instance, class) pair."""
        inst = (wm_class[0] if wm_class and len(wm_class) > 0 else "") or ""
        clss = (wm_class[1] if wm_class and len(wm_class) > 1 else "") or ""
        low = {c.lower() for c in KIOSK_WM_CLASSES}
        if inst.lower() in low or clss.lower() in low:
            return True
        return (name or "") in KIOSK_WM_NAMES

    @staticmethod
    def decide(wm_class, name):
        """(known, native) for a foreground window's identity.  FAIL-SAFE: a
        window whose class AND name are unreadable (already-destroyed xid at
        startup, a not-yet-mapped window, an X error) is (False, False) —
        UNKNOWN IS NEVER NATIVE, because 'native' is what B is allowed to
        close.  The live bug this guards (2026-06-07): at session relaunch the
        root's _NET_ACTIVE_WINDOW still pointed at the DEAD kiosk's xid, ident
        failed, 'native' stuck (the new kiosk REUSED the xid so the
        same-xid fast path never re-probed), and every desktop B politely
        closed the kiosk — a relaunch loop.  Unknown idents are also re-probed
        every poll until they read (see refresh)."""
        known = (wm_class is not None) or bool(name)
        return known, (known and not NativeWatch.classify(wm_class, name))

    def mark_foreground(self, xid, is_native, game_running):
        """Maximize-once decision for a newly-foreground window (pure logic —
        the X side effect lives in _maximize).  Returns 'maximize' | None."""
        if not xid or xid in self._seen:
            return None
        self._seen.add(xid)
        if len(self._seen) > 256:        # bound growth across long sessions
            self._seen = set(list(self._seen)[-128:])
        if is_native and not game_running:
            return "maximize"
        return None

    # -- X helpers (lock held by callers' public methods) ----------------------
    def _win(self, xid):
        return self.d.create_resource_object("window", xid)

    def _client_message(self, xid, type_name, data):
        ev = self._proto.event.ClientMessage(
            window=self._win(xid), client_type=self._atom[type_name],
            data=(32, (list(data) + [0] * 5)[:5]))
        self.root.send_event(ev, event_mask=(self._X.SubstructureRedirectMask |
                                             self._X.SubstructureNotifyMask))
        self.d.flush()

    def _active_xid(self):
        p = self.root.get_full_property(self._atom["_NET_ACTIVE_WINDOW"],
                                        self._X.AnyPropertyType)
        return int(p.value[0]) if (p and p.value and p.value[0]) else 0

    def _ident(self, xid):
        w = self._win(xid)
        try:
            cls = w.get_wm_class()
        except Exception:
            cls = None
        name = None
        try:
            p = w.get_full_property(self._atom["_NET_WM_NAME"],
                                    self._atom["UTF8_STRING"])
            name = p.value.decode("utf-8", "replace") if (p and p.value) else None
            if not name:
                name = w.get_wm_name()
        except Exception:
            pass
        return cls, name

    def _maximize(self, xid):
        # _NET_WM_STATE: [action=1(add), prop1, prop2, source=2(pager)]
        self._client_message(xid, "_NET_WM_STATE",
                             [1, self._atom["_NET_WM_STATE_MAXIMIZED_VERT"],
                              self._atom["_NET_WM_STATE_MAXIMIZED_HORZ"], 2])

    # -- the poll (daemon thread) ----------------------------------------------
    def refresh(self):
        try:
            with self._lock:
                xid = self._active_xid()
                if not xid:
                    self.fg_xid, self.fg_native, self._fg_known = 0, False, True
                    return
                if xid == self.fg_xid and self._fg_known:
                    return          # unchanged foreground, identity already read.
                                    # An UNKNOWN ident re-probes every poll: X ids
                                    # get REUSED, so "same xid" is not "same window"
                                    # until we have actually read who it is.
                cls, name = self._ident(xid)
                known, native = self.decide(cls, name)
                prev = (self.fg_xid, self.fg_native, self._fg_known)
                self.fg_xid, self.fg_native, self._fg_known = xid, native, known
                self.fg_label = "%s/%s" % ((cls[0] if cls else "?"), name or "?")
                if not known:
                    if prev != (xid, native, known):
                        log("foreground 0x%x identity unreadable -> treated as "
                            "SHELL (B passes through; re-probing)" % xid)
                    return          # never close/maximize what we can't identify
                act = self.mark_foreground(xid, native,
                                           bool(self.game_check()))
                if act == "maximize":
                    self._maximize(xid)
                    log("native window 0x%x (%s) -> maximized (app-class policy)"
                        % (xid, self.fg_label))
                elif native and prev != (xid, native, known):
                    log("native window foreground: 0x%x (%s)" % (xid, self.fg_label))
        except Exception as e:
            # fail SAFE: unknown foreground = treat as kiosk (normal key flow)
            self.fg_xid, self.fg_native, self._fg_known = 0, False, True
            log("native watch refresh failed: %s" % e)

    def start(self):
        self.refresh()
        def _loop():
            while True:
                time.sleep(NATIVE_POLL_S)
                self.refresh()
        _threading.Thread(target=_loop, daemon=True).start()
        return self

    # -- hot-path read + actions ------------------------------------------------
    def is_native_fg(self):
        return self.fg_native

    def close_fg(self):
        xid = self.fg_xid
        if not xid:
            return
        with self._lock:
            # _NET_CLOSE_WINDOW: [timestamp=0, source=2(pager)]
            self._client_message(xid, "_NET_CLOSE_WINDOW", [0, 2])

    def activate_kiosk(self):
        with self._lock:
            p = self.root.get_full_property(self._atom["_NET_CLIENT_LIST"],
                                            self._X.AnyPropertyType)
            kiosk = None
            for xid in (p.value if (p and p.value) else []):
                cls, name = self._ident(int(xid))
                inst = (cls[0] if cls else "") or ""
                if inst.lower() == "kiosk.py" or (name or "") == "GOSE":
                    kiosk = int(xid)
                    break
            if kiosk is None:
                raise RuntimeError("kiosk window not found in _NET_CLIENT_LIST")
            # _NET_ACTIVE_WINDOW: [source=2(pager), timestamp=0, current-active=0]
            self._client_message(kiosk, "_NET_ACTIVE_WINDOW", [2, 0, 0])


def make_native_watch(game_check):
    """NativeWatch when the vendored Xlib can connect; else None (honest no-op:
    the bridge then behaves exactly as before this feature)."""
    try:
        nw = NativeWatch(":0", game_check=game_check).start()
        log("native watch: ON (foreground poll %.1fs; B closes / Guide rescues)"
            % NATIVE_POLL_S)
        return nw
    except Exception as e:
        log("native watch: OFF (%s) — native-app discipline unavailable" % e)
        return None


# ---------------------------------------------------------------------------
class AdminGate:
    """Decides whether a given evdev device may drive the OS menus.

    Source of truth = the GOSE server's controller registry (GET /controllers),
    which lists every connected pad with a stable `id` (= sysfs node basename,
    e.g. "input34"), its `/dev/input/eventN` `path`, `source` (virtual/bluetooth/
    native) and `is_dev`.  We map each evdev device we've opened to a registry
    entry BY PATH (the daemon keys devices by /dev/input/eventN, identical to the
    registry's `path`), then allow it iff it is:

      * the OS-admin controller (registry `admin`, settable via the Hub /
        os_admin_controller.json), OR
      * the dev pad (registry `is_dev` -- the original seat-1 virtual pad, so
        Wren/dev always drives), OR
      * an admin-tier AI's seat pad (see below).

    AI-admin handling (best-effort, honest limits):
      ai_tokens.json maps token -> {name, tier[, seat]}.  An admin-tier grant
      (tier=="admin") WITH a seat is mapped seat N -> the N-th virtual pad in js
      order (AI seat pads share the Xbox-360 IDENTITY/GUID and differ only by name
      "AI virtual controller 1..4", so js order is the discriminator the gate uses
      -- this ordering matches the agent's
      seat-open order but is not cryptographically tied to the seat).  If a
      seated admin grant's seat is out of range of the detected virtual pads we
      cannot map it cleanly, so we fall open for ALL virtual pads (noted in the
      log).  A NO-seat admin grant is NOT treated as "allow all virtual": such an
      AI drives via the dev/seat-1 pad (already always-allowed), and opening
      every virtual pad would let an unrelated 2nd seat drive the OS.

    Fail-open: if the registry is unreachable we allow everything, so nav never
    breaks because the server hiccuped.  Refreshed every GATE_REFRESH_S.
    """

    def __init__(self, refresh_s=GATE_REFRESH_S,
                 fetch_controllers=None, read_admin_file=None, read_ai_tokens=None,
                 read_oobe_done=None):
        self.refresh_s = refresh_s
        self._fetch_controllers = fetch_controllers or self._default_fetch
        self._read_admin_file = read_admin_file or self._default_admin_file
        self._read_ai_tokens = read_ai_tokens or self._default_ai_tokens
        self._read_oobe_done = read_oobe_done or self._default_oobe_done
        self._t = 0.0
        self._state = None     # computed dict, or None == unreachable (fail-open)

    # --- default real-world sources (overridable for tests) -----------------
    @staticmethod
    def _default_fetch():
        with urllib.request.urlopen(SERVER_URL + "/controllers", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8"))

    @staticmethod
    def _default_admin_file():
        try:
            return (json.load(open(OS_ADMIN_FILE)) or {}).get("id")
        except Exception:
            return None

    @staticmethod
    def _default_ai_tokens():
        try:
            return json.load(open(AI_TOKENS_FILE)) or {}
        except Exception:
            return {}

    @staticmethod
    def _default_oobe_done():
        return os.path.exists(OOBE_DONE_FILE)

    # --- registry refresh ---------------------------------------------------
    def _rebuild(self):
        try:
            data = self._fetch_controllers() or {}
        except Exception:
            self._state = None     # unreachable -> fail open
            return
        pads = data.get("controllers") or []
        admin_id = data.get("admin") or self._read_admin_file()
        dev_ids = {p.get("id") for p in pads if p.get("is_dev")}
        virt = sorted((p for p in pads if p.get("source") == "virtual"),
                      key=lambda p: p.get("js", 0))

        ai_seat_ids = set()
        allow_all_virtual = False
        ai_admin = False
        try:
            toks = self._read_ai_tokens() or {}
        except Exception:
            toks = {}
        for rec in toks.values():
            if not isinstance(rec, dict) or rec.get("tier") != "admin":
                continue
            ai_admin = True
            seat = rec.get("seat")
            if not seat:
                continue                       # no seat -> drives via dev pad (already allowed)
            try:
                seat = int(seat)
            except Exception:
                continue
            if 1 <= seat <= len(virt):
                ai_seat_ids.add(virt[seat - 1].get("id"))
            else:
                allow_all_virtual = True       # seated admin AI, seat unmappable -> best-effort

        # docs/25 §5.2b: PRE-USER there is no admin yet, so ANY detected pad must be able to
        # navigate the first-boot wizard (the pad that completes setup becomes the admin
        # candidate). Only while no admin is set AND the OOBE flag is absent. Once an admin
        # exists or setup is done, normal arbitration resumes.
        try:
            oobe_done = bool(self._read_oobe_done())
        except Exception:
            oobe_done = True       # unknown -> assume done (don't accidentally open the OS)
        pre_admin = (admin_id is None) and (not oobe_done)

        self._state = {
            "admin_id": admin_id,
            "dev_ids": dev_ids,
            "ai_seat_ids": ai_seat_ids,
            "allow_all_virtual": allow_all_virtual,
            "ai_admin": ai_admin,
            "pre_admin": pre_admin,
            "by_path": {p.get("path"): p for p in pads if p.get("path")},
        }

    def _maybe_refresh(self):
        now = time.time()
        if now - self._t > self.refresh_s:
            self._rebuild()
            self._t = now

    # --- decision (the gate) ------------------------------------------------
    def allows(self, dev_path, dev_name=None):
        """Return (allowed: bool, reason: str) for the device at dev_path."""
        self._maybe_refresh()
        st = self._state
        if st is None:
            return True, "fail-open (registry unreachable)"
        if st.get("pre_admin"):
            return True, "first-boot: no admin yet — any pad drives the wizard"
        entry = st["by_path"].get(dev_path)
        if entry is None:
            return True, "fail-open (unmapped: %s)" % (dev_name or dev_path)
        cid = entry.get("id")
        if cid == st["admin_id"]:
            return True, "os-admin"
        if entry.get("is_dev") or cid in st["dev_ids"]:
            return True, "dev"
        if cid in st["ai_seat_ids"]:
            return True, "ai-admin seat"
        if st["allow_all_virtual"] and entry.get("source") == "virtual":
            return True, "ai-admin (all-virtual fallback)"
        return False, "not OS-admin"


# ---------------------------------------------------------------------------
# UNIVERSAL NORMALIZATION  (SDL_GameControllerDB-driven, evdev-native)
# ---------------------------------------------------------------------------
def _sdl_guid(bustype, vendor, product, version):
    """The 16-byte SDL2 joystick GUID (hex) for a bus device, zero-crc form —
    the format used by gamecontrollerdb.txt rows (e.g. an Xbox 360 pad
    045e:028e bus3 v0110 -> 030000005e0400008e02000010010000)."""
    return struct.pack("<HHHHHHHH",
                       bustype & 0xFFFF, 0, vendor & 0xFFFF, 0,
                       product & 0xFFFF, 0, version & 0xFFFF, 0).hex()


def _guid_zero_crc(guid):
    """Same GUID with bytes 2-3 (the optional name-crc16) zeroed, so a modern
    SDL dump that embeds a crc still matches a classic zero-crc DB row."""
    return guid[:4] + "0000" + guid[8:] if len(guid) == 32 else guid


def _sdl_button_index_map(keycodes):
    """Reproduce SDL2's Linux evdev->joystick-button-index enumeration
    (src/joystick/linux/SDL_sysjoystick.c): scan [BTN_JOYSTICK..KEY_MAX) then
    [BTN_MISC..BTN_JOYSTICK), assigning b0,b1,... in that order. This is what
    gamecontrollerdb's `bN` values are indexed against on Linux."""
    order = []
    for c in range(ecodes.BTN_JOYSTICK, ecodes.KEY_MAX):
        if c in keycodes:
            order.append(c)
    for c in range(ecodes.BTN_MISC, ecodes.BTN_JOYSTICK):
        if c in keycodes:
            order.append(c)
    return {c: i for i, c in enumerate(order)}


def _sdl_axis_index_map(abscodes):
    """SDL2's Linux evdev->joystick-axis-index enumeration: ascending ABS code,
    skipping the ABS_HAT* pairs (SDL treats those as hats h0..h3, not axes)."""
    axes = [c for c in sorted(abscodes)
            if not (ecodes.ABS_HAT0X <= c <= ecodes.ABS_HAT3Y)]
    return {c: i for i, c in enumerate(axes)}


class _NEvent(object):
    """Tiny read-only event shim (the only thing the bridge reads off an event is
    .type/.code/.value), used when the normalizer rewrites a raw code."""
    __slots__ = ("type", "code", "value")

    def __init__(self, t, c, v):
        self.type, self.code, self.value = t, c, v


class ControllerDB:
    """Parsed SDL_GameControllerDB (Linux rows) keyed by SDL GUID.

    entry_for(bustype,vendor,product,version) -> {std_name: ("b", idx) | ("a", idx)}
    for the button/axis bindings of that controller (hats are already the standard
    ABS_HAT0, so we don't need them here), or None when the pad isn't in the DB.
    """

    def __init__(self, paths=GCDB_PATHS, text=None):
        self.by_guid = {}      # exact GUID -> bindings
        self.by_guid_z = {}    # zero-crc GUID -> bindings
        self.path = None
        self.count = 0
        if text is not None:
            self._parse(text)
        else:
            for p in paths:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as fh:
                        self._parse(fh.read())
                    self.path = p
                    break
                except Exception:
                    continue

    def _parse(self, text):
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "platform:Linux" not in line:
                continue
            parts = line.split(",")
            if len(parts) < 3 or len(parts[0]) != 32:
                continue
            guid = parts[0].lower()
            binds = {}
            for fld in parts[2:]:
                if ":" not in fld:
                    continue
                name, val = fld.split(":", 1)
                if not val:
                    continue
                if val[0] == "b" and val[1:].isdigit():
                    binds[name] = ("b", int(val[1:]))
                elif val[0] == "a":
                    num = val[1:].lstrip("+-").rstrip("~")
                    if num.isdigit():
                        binds[name] = ("a", int(num))
                # hats (hN.M) intentionally skipped: ABS_HAT0 is already standard
            if binds:
                self.by_guid[guid] = binds
                self.by_guid_z.setdefault(_guid_zero_crc(guid), binds)
                self.count += 1

    def entry_for(self, bustype, vendor, product, version):
        guid = _sdl_guid(bustype, vendor, product, version)
        return (self.by_guid.get(guid)
                or self.by_guid_z.get(_guid_zero_crc(guid)))


class Normalizer:
    """Turns ANY pad into the bridge's standard evdev language.

    For a pad that already reports position-standard codes (BTN_SOUTH present —
    true for every kernel-driver pad: Xbox/DS4/DualSense/Switch/8BitDo, AND the
    virtual Xbox-360 test pad) the remap is EMPTY -> events pass through untouched
    (the proven, zero-regression path). For a non-standard pad (no BTN_SOUTH) that
    the DB knows, we build a raw-evdev-code -> standard-evdev-code remap from the
    DB row, so its Cross/A becomes BTN_SOUTH, its d-pad buttons become BTN_DPAD_*,
    its sticks become ABS_X/Y, etc. Unknown non-standard pads keep their raw codes
    (sane evdev defaults). Per-device remap is cached by dev path.
    """

    def __init__(self, db=None):
        self.db = db if db is not None else ControllerDB()
        self._cache = {}      # dev_path -> remap dict (possibly empty)

    @staticmethod
    def build_remap(db, bustype, vendor, product, version, keycodes, abscodes):
        if ecodes.BTN_SOUTH in keycodes:
            return {}          # position-standard kernel pad -> identity
        entry = db.entry_for(bustype, vendor, product, version)
        if not entry:
            return {}          # unknown pad -> sane evdev defaults (identity)
        inv_btn = {i: c for c, i in _sdl_button_index_map(keycodes).items()}
        inv_axis = {i: c for c, i in _sdl_axis_index_map(abscodes).items()}
        remap = {}
        for std, (kind, idx) in entry.items():
            if kind == "b":
                raw = inv_btn.get(idx)
                tgt = STD_BTN_TO_EVDEV.get(std)
                if raw is not None and tgt is not None and raw != tgt:
                    remap[(ecodes.EV_KEY, raw)] = tgt
            elif kind == "a":
                raw = inv_axis.get(idx)
                tgt = STD_AXIS_TO_EVDEV.get(std)
                if raw is not None and tgt is not None and raw != tgt:
                    remap[(ecodes.EV_ABS, raw)] = tgt
        return remap

    def for_device(self, dev):
        path = getattr(dev, "path", None) or repr(dev)
        if path in self._cache:
            return self._cache[path]
        try:
            info = dev.info
            caps = dev.capabilities()
            keycodes = set(caps.get(ecodes.EV_KEY, []) or [])
            abscodes = set(c for (c, _info) in (caps.get(ecodes.EV_ABS, []) or []))
        except Exception:
            self._cache[path] = {}
            return {}
        remap = self.build_remap(self.db, info.bustype, info.vendor,
                                 info.product, info.version, keycodes, abscodes)
        self._cache[path] = remap
        guid = _sdl_guid(info.bustype, info.vendor, info.product, info.version)
        known = self.db.entry_for(info.bustype, info.vendor, info.product,
                                  info.version) is not None
        log("normalize %r guid=%s db=%s remap=%d %s" % (
            getattr(dev, "name", "?"), guid,
            "known" if known else "unknown", len(remap),
            "(identity)" if not remap else ""))
        return remap

    def normalize(self, event, dev):
        remap = self.for_device(dev)
        if not remap:
            return event
        tgt = remap.get((event.type, event.code))
        if tgt is None:
            return event
        return _NEvent(event.type, tgt, event.value)


def is_gamepad(dev, db=None):
    """True only for the BUTTON interface of a controller (see
    GAMEPAD_BTN_RANGES). A buttonless or wrong-button sibling node (motion
    sensors, touchpad, headset jack) is rejected no matter what its name says
    — every real pad (kernel-driver, virtual, generic/DInput, DB-known or not)
    has buttons inside the accepted ranges, so nothing legitimate is lost."""
    try:
        keys = dev.capabilities().get(ecodes.EV_KEY, []) or []
    except Exception:
        keys = []
    return any(lo <= c < hi for c in keys for (lo, hi) in GAMEPAD_BTN_RANGES)


# ---------------------------------------------------------------------------
class PadNav:
    """Maps controller events to keysyms and emits them via `emit`, unless a
    game is running (`game_check`).  map_event() is pure (no X) -> unit-testable.
    """

    def __init__(self, emit=None, game_check=None, deadzone=STICK_DEADZONE,
                 gate=None, gamebar_check=None, wm=None, wm_check=None,
                 pointer=None, native=None):
        self.emit = emit or xdotool_emit
        self.game_check = game_check or default_game_running
        self.gamebar_check = gamebar_check or default_gamebar_open
        self.wm_check = wm_check or default_wm_open
        # the native-app discipline watcher (docs/27 §4.1); None disables it
        self.native = native
        # gate(dev_path, dev_name) -> (allowed, reason).  Default: allow all
        # (so unit tests / un-wired use don't gate); run() wires AdminGate.allows.
        self.gate = gate or (lambda path, name=None: (True, "no-gate"))
        # the WM modal layer (docs/23 §7); None disables it (pure-nav tests)
        self.wm = wm
        self.deadzone = deadzone
        self.axis_dirs = {}    # ecode -> "neg"/"pos"/None  (current crossed dir)
        self.repeat = {}       # ecode -> {"key": keysym, "next": float}
        self._game_cache = (0.0, False)
        # --- stick cursor (docs/27 §1.1): pointer engine + deflection state ---
        # pointer=None disables the cursor entirely (pure key-nav tests).
        self.pointer = pointer
        self.stick = {"x": 0.0, "y": 0.0}   # normalized deflection -1..1 (post-deadzone)
        self.cursor_last_move = 0.0         # ts of last pointer motion we sent
        self.cursor_visible = True          # our view of the X cursor's XFixes state
        self._cursor_t = None               # last cursor tick ts (dt anchor)
        self._fx = self._fy = 0.0           # sub-pixel motion accumulators
        self._move_count = 0                # motions since last FPS log line
        self._fps_t = 0.0                   # last FPS log line ts
        self._cursor_blocked_log = None     # last logged block reason (de-spam)
        self._gate_denied_logged = set()    # dev paths whose stick we logged once
        # --- external-wake (task 26): a REAL mouse must wake a hidden cursor --
        self._self_moved = False            # we moved the pointer since last check
        self._ptr_pos = None                # wake baseline; None = re-baseline first

    # --- pause rule ---------------------------------------------------------
    def is_paused(self):
        now = time.time()
        t, v = self._game_cache
        if now - t > GAME_CACHE_S:
            try:
                v = bool(self.game_check())
            except Exception:
                v = False
            self._game_cache = (now, v)
        return v

    def is_suppressed(self):
        """Emits are suppressed only when a game runs AND no overlay layer is open.
        The Game Bar overlay (its flag file) is the exception that lets the pad
        drive the bar even while the game process is alive; the WM modal layer's
        flag (/tmp/gose-wm-open) mirrors it, so window ops work over a game too."""
        if not self.is_paused():
            return False
        try:
            if self.gamebar_check():
                return False        # game bar open -> drive the bar, don't suppress
        except Exception:
            pass
        try:
            if self.wm_check():
                return False        # WM layer open -> window ops over the game
        except Exception:
            pass
        return True

    # --- pure mapping (no X) ------------------------------------------------
    def _axis_dir(self, ec, value):
        cfg = AXIS_MAP[ec]
        if cfg.get("stick"):
            if value <= -self.deadzone:
                return "neg"
            if value >= self.deadzone:
                return "pos"
            return None
        # hat: ternary
        if value < 0:
            return "neg"
        if value > 0:
            return "pos"
        return None

    def map_event(self, event):
        """Return list of keysyms this event should produce (no emission).
        Side effect: updates self.axis_dirs / self.repeat for axis crossings."""
        et, ec, val = event.type, event.code, event.value
        if et == ecodes.EV_KEY:
            if val == 1 and ec in BUTTON_KEYMAP:   # key-down only -> debounce
                return [BUTTON_KEYMAP[ec]]
            return []
        if et == ecodes.EV_ABS and ec in AXIS_MAP:
            d = self._axis_dir(ec, val)
            if self.axis_dirs.get(ec) == d:
                return []
            self.axis_dirs[ec] = d
            if d is None:
                self.repeat.pop(ec, None)
                return []
            keysym = AXIS_MAP[ec][d]
            self.repeat[ec] = {"key": keysym, "next": time.time() + REPEAT_INITIAL}
            return [keysym]
        return []

    # --- dispatch (applies suppression + emits; used by tick auto-repeat) ----
    def _dispatch(self, keysyms):
        if not keysyms:
            return
        if self.is_suppressed():
            for k in keysyms:
                log("PAUSED (game running) -> suppressed %s" % k)
            return
        for k in keysyms:
            self.emit(k)
            log("emit key %s" % k)

    def feed(self, event, dev_path=None, dev_name=None):
        keysyms = self.map_event(event)
        # -0.5) native-app escape hatch (docs/27 §4.1): Guide over a foreground
        #       native app re-activates the kiosk FIRST, then falls through so
        #       the WM layer opens the carousel as usual (user lands in the
        #       switcher, on top).  Same gate as the WM layer; silent in games.
        if (self.native is not None and event.type == ecodes.EV_KEY
                and event.code == ecodes.BTN_MODE and event.value == 1
                and self.native.is_native_fg() and not self.is_paused()):
            allowed, _reason = self.gate(dev_path, dev_name)
            if allowed:
                try:
                    self.native.activate_kiosk()
                    log("native fg + Guide -> kiosk re-activated (escape hatch)")
                except Exception as e:
                    log("kiosk re-activate failed: %s" % e)
        # 0) WM modal layer (chunk B): owns Guide + the L2 modifier, and while a
        #    modal is open it owns the whole pad — semantic events are POSTed to
        #    /wm/event instead of keys being synthesized.
        if self.wm and self.wm.handle(event, dev_path, dev_name, keysyms):
            if event.type == ecodes.EV_ABS:
                self.repeat.pop(event.code, None)   # no key auto-repeat from a consumed axis
            return
        # 0.5) LEFT STICK -> pointer cursor (docs/27 §1.1): deflection state only;
        #      the actual motion happens in tick() at >=60Hz (suppression/WM-modal
        #      are re-checked there, so a game start mid-deflection stops motion).
        if event.type == ecodes.EV_ABS and event.code in CURSOR_AXES:
            self._cursor_feed(event, dev_path, dev_name)
            return
        if not keysyms:
            return
        # 1) suppression: a game owns the pad UNLESS the Game Bar overlay is open.
        if self.is_suppressed():
            for k in keysyms:
                log("PAUSED (game running) -> suppressed %s" % k)
            if event.type == ecodes.EV_ABS:
                self.repeat.pop(event.code, None)   # don't auto-repeat a held stick
            return
        # 2) admin gate: only the OS-admin / dev / admin-AI pad drives the menus.
        allowed, reason = self.gate(dev_path, dev_name)
        if not allowed:
            log("ignored: %s %s" % (dev_name or dev_path or "?", reason))
            if event.type == ecodes.EV_ABS:
                self.repeat.pop(event.code, None)   # blocked pad must not queue repeats
            return
        # 2.5) native-app discipline (docs/27 §4.1): B while a native app is
        #      foreground (and no game runs) closes THAT window politely via
        #      _NET_CLOSE_WINDOW — an XTEST Escape would go INTO the app and
        #      mean whatever the app wants (the browser-trap).  During a game
        #      this never fires (suppression already returned above).
        if (self.native is not None and "Escape" in keysyms
                and self.native.is_native_fg() and not self.is_paused()):
            try:
                self.native.close_fg()
                log("native fg + B -> polite close (_NET_CLOSE_WINDOW)")
            except Exception as e:
                log("native close failed: %s" % e)
            return
        # 3) A at an ACTIVE cursor clicks at the pointer instead of Enter
        #    (cursor active = pointer moved within CURSOR_CLICK_WINDOW; BTN_START
        #    stays Return always, so focus-nav Accept is never lost).
        if (self.pointer is not None and event.type == ecodes.EV_KEY
                and event.code == ecodes.BTN_SOUTH and event.value == 1
                and self.cursor_active()):
            try:
                self.pointer.click(1)
                log("cursor click (A @ active pointer)")
            except Exception as e:
                log("cursor click FAILED (%s) -> Return fallback" % e)
                self.emit("Return")
            return
        for k in keysyms:
            self.emit(k)
            log("emit key %s" % k)

    # --- stick cursor (docs/27 §1.1) -----------------------------------------
    def _norm_deflect(self, value):
        """Normalized post-deadzone deflection: 0.0 inside STICK_DEADZONE, then
        LINEAR to ±1.0 at full deflection (the owner-confirmed gentle accel)."""
        if abs(value) <= self.deadzone:
            return 0.0
        n = (abs(value) - self.deadzone) / float(STICK_FULL - self.deadzone)
        return min(1.0, n) * (1.0 if value > 0 else -1.0)

    def _cursor_feed(self, event, dev_path, dev_name):
        """Record left-stick deflection (motion itself happens in tick())."""
        if self.pointer is None:
            return
        norm = self._norm_deflect(event.value)
        if norm != 0.0:
            allowed, reason = self.gate(dev_path, dev_name)
            if not allowed:
                if dev_path not in self._gate_denied_logged:
                    self._gate_denied_logged.add(dev_path)
                    log("cursor ignored: %s %s" % (dev_name or dev_path or "?", reason))
                norm = 0.0
        axis = "x" if event.code == ecodes.ABS_X else "y"
        self.stick[axis] = norm

    def cursor_deflected(self):
        return bool(self.stick["x"] or self.stick["y"])

    def cursor_release(self):
        """Zero the deflection state — called when a device detaches so a pad
        unplugged mid-deflection can't leave the pointer drifting forever."""
        self.stick["x"] = self.stick["y"] = 0.0

    def cursor_active(self, now=None):
        """True while the pointer moved within CURSOR_CLICK_WINDOW -> A clicks."""
        return ((now or time.time()) - self.cursor_last_move) <= CURSOR_CLICK_WINDOW

    def _cursor_blocked(self):
        """Why cursor motion must not happen right now (None = go).  The WM
        modal layer owns the WHOLE pad while open (docs/27 §4 layer 3); a
        running game suppresses motion exactly like keys (layer 1)."""
        if self.wm is not None and self.wm.mode:
            return "wm modal"
        try:
            if self.wm_check():
                return "wm flag"
        except Exception:
            pass
        if self.is_suppressed():
            return "game running"
        return None

    # --- cursor visibility transitions (task 26: never stuck invisible) ------
    def _cursor_show(self, why):
        """The ONLY show transition.  The flag flips to visible FIRST so an
        exception in the X call cannot strand the state machine hidden — a
        wrong 'visible' flag merely costs a redundant hide later (the engine's
        per-connection idempotence makes re-issued calls harmless), while a
        wrong 'hidden' flag would freeze the cursor invisible, the exact bug
        class this kills.  An engine reconnect auto-reveals regardless (XFixes
        hides die with their client), so every failure path ends VISIBLE."""
        if self.cursor_visible:
            return
        self.cursor_visible = True
        try:
            self.pointer.show_cursor()
            log("cursor SHOW (%s)" % why if self.pointer.can_hide
                else "cursor active (engine cannot hide/show; X cursor stays as-is)")
        except Exception as e:
            log("cursor show failed (%s) -> engine reconnect will auto-reveal" % e)

    def _cursor_hide(self, why):
        """The ONLY hide transition.  The flag flips only AFTER the X call
        succeeds: a failed hide leaves us honestly visible and simply retries
        next tick — never a hidden screen behind a 'visible' flag."""
        if not self.cursor_visible:
            return
        try:
            self.pointer.hide_cursor()
            self.cursor_visible = False
            log("cursor HIDE (%s)" % why)
        except Exception as e:
            log("cursor hide failed (%s) -> stays visible" % e)

    def _external_wake(self, now):
        """docs/27 §1.1 wake rule (task 26): pointer motion the bridge did NOT
        make (a real host mouse/tablet — or any other X client) wakes the
        cursor: SHOW if hidden + idle-timer reset.  A pressed pointer button
        wakes it too (failsafe).  Runs on the EXISTING tick cadence — one
        QueryPointer round-trip (0.77 ms measured in-guest), and NOT EVEN THAT
        on the 60Hz stick path: after any tick in which we moved the pointer
        ourselves the check only invalidates its baseline (no query, no
        judgement), so our own XTEST motion can never read as external (which
        would reset the idle timer forever and the cursor would never hide).
        While suppressed (game owns the pad / WM modal) it neither queries nor
        wakes, and the baseline dies — so motion accumulated during a game
        cannot fire a phantom SHOW at game exit."""
        if not self.pointer.can_hide:
            return                  # cursor can never be hidden -> nothing to wake
        if self._self_moved:
            self._self_moved = False
            self._ptr_pos = None    # our own XTEST motion -> re-baseline later
            return
        if self._cursor_blocked() is not None:
            self._ptr_pos = None    # suppressed: no wake; stale baselines die
            return
        try:
            pos, buttons = self.pointer.pointer_state()
        except Exception:
            self._ptr_pos = None    # can't read -> never guess; re-baseline later
            return
        if self._ptr_pos is None:
            self._ptr_pos = pos     # re-baseline only; judge from the NEXT tick
            return
        moved = pos != self._ptr_pos
        self._ptr_pos = pos
        if moved or buttons:
            self.cursor_last_move = now      # reset the idle timer
            self._cursor_show("external motion" if moved else "pointer button")

    def _cursor_tick(self, now):
        if self.pointer is None:
            return
        dt = 0.0 if self._cursor_t is None else min(now - self._cursor_t, 0.1)
        self._cursor_t = now
        if self.cursor_deflected() and dt > 0.0:
            blocked = self._cursor_blocked()
            if blocked:
                if self._cursor_blocked_log != blocked:
                    self._cursor_blocked_log = blocked
                    log("cursor suppressed (%s)" % blocked)
            else:
                if self._cursor_blocked_log:
                    self._cursor_blocked_log = None
                self._fx += self.stick["x"] * CURSOR_MAX_SPEED * dt
                self._fy += self.stick["y"] * CURSOR_MAX_SPEED * dt
                dx, dy = int(self._fx), int(self._fy)
                if dx or dy:
                    self._fx -= dx
                    self._fy -= dy
                    self._cursor_show("stick motion")
                    try:
                        self.pointer.move(dx, dy)
                        self.cursor_last_move = now
                        self._self_moved = True   # OUR motion: never "external"
                        self._move_count += 1
                    except Exception as e:
                        log("cursor move failed: %s" % e)
        # once-per-second motion-rate evidence while the cursor is in use
        if self._move_count and now - self._fps_t >= 1.0:
            if self._fps_t:
                log("cursor rate: %d updates in %.2fs" % (self._move_count,
                                                          now - self._fps_t))
            self._fps_t = now
            self._move_count = 0
        # wake on EXTERNAL pointer activity (real mouse / button) — checked
        # BEFORE the hide so a fresh wake resets the idle timer this same tick
        self._external_wake(now)
        # auto-hide after idle (honest visibility: XFixes only — parking the
        # pointer is FORBIDDEN, it fires hover side-effects)
        if (self.cursor_visible and self.pointer.can_hide
                and now - self.cursor_last_move > CURSOR_HIDE_S):
            self._cursor_hide("idle %.1fs" % (now - self.cursor_last_move)
                              if self.cursor_last_move else "startup, unused")

    def tick(self):
        """Auto-repeat held directions + cursor motion. Call frequently (the
        select() timeout shrinks to CURSOR_TICK_S while the stick is deflected)."""
        now = time.time()
        due = []
        for ec, st in self.repeat.items():
            if now >= st["next"]:
                due.append(st["key"])
                st["next"] = now + REPEAT_INTERVAL
        self._dispatch(due)
        self._cursor_tick(now)

    def next_repeat_timeout(self, default):
        if not self.repeat:
            return default
        now = time.time()
        soonest = min(st["next"] for st in self.repeat.values())
        return max(0.0, min(default, soonest - now))

    def next_timeout(self, default):
        """select() timeout: repeat-aware, and 16ms while the stick is deflected
        (>=60Hz cursor) WITHOUT busy-spinning at idle; while visible-and-idle it
        wakes just in time for the auto-hide."""
        t = self.next_repeat_timeout(default)
        if self.pointer is not None:
            if self.cursor_deflected() and self._cursor_blocked() is None:
                # 60Hz only while motion can actually happen; a deflected stick
                # during a game (blocked) must not busy-wake the loop
                t = min(t, CURSOR_TICK_S)
            elif self.cursor_visible and self.pointer.can_hide and self.cursor_last_move:
                t = min(t, max(0.05, (self.cursor_last_move + CURSOR_HIDE_S)
                               - time.time()))
        return t


# ---------------------------------------------------------------------------
def run():
    log("gose-pad-nav starting (pid %d)" % os.getpid())
    # stale WM flag from a previous crash would unsuppress keys during games
    try:
        os.remove(WM_FLAG)
    except Exception:
        pass
    _threading.Thread(target=_wm_post_worker, daemon=True).start()
    engine = make_engine()              # XTEST (persistent X conn) or xdotool
    gate = AdminGate().allows
    gamewatch = GameWatch().start()     # off-hot-path pgrep (shared instance)
    nav = PadNav(emit=engine.key, pointer=engine,
                 game_check=gamewatch.is_running,
                 gate=gate, wm=WMLayer(gate=gate),
                 native=make_native_watch(gamewatch.is_running))
    db = ControllerDB()
    norm = Normalizer(db)
    log("controller DB loaded: %s (%d Linux entries)" % (db.path, db.count))
    devices = {}   # path -> InputDevice
    last_scan = 0.0

    def scan():
        for path in glob.glob("/dev/input/event*"):
            if path in devices:
                continue
            try:
                dev = evdev.InputDevice(path)
            except Exception:
                continue
            if is_gamepad(dev, db):
                try:
                    dev.grab  # noqa: we intentionally do NOT grab -- RetroArch
                              # needs the same evdev when a game runs.
                except Exception:
                    pass
                devices[path] = dev
                log("attached %s (%r)" % (path, dev.name))
            else:
                dev.close()

    scan()
    if not devices:
        log("no gamepad yet -- will keep polling /dev/input")

    while True:
        now = time.time()
        if now - last_scan > DEVICE_RESCAN_S:
            scan()
            last_scan = now

        fds = {dev.fd: path for path, dev in devices.items()}
        timeout = nav.next_timeout(DEVICE_RESCAN_S)
        try:
            r, _, _ = select.select(list(fds.keys()), [], [], timeout)
        except (OSError, ValueError):
            # a device fd went away -> drop dead devices and rescan
            for path in list(devices.keys()):
                if not os.path.exists(path):
                    log("detached %s" % path)
                    try:
                        devices[path].close()
                    except Exception:
                        pass
                    del devices[path]
            nav.cursor_release()   # an unplug mid-deflection must not leave drift
            continue

        for fd in r:
            path = fds[fd]
            dev = devices.get(path)
            if dev is None:
                continue
            try:
                for event in dev.read():
                    if event.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                        # UNIVERSAL NORMALIZATION: rewrite this pad's raw codes to
                        # the bridge's standard evdev language (identity for every
                        # position-standard pad, incl. the virtual test pad).
                        nav.feed(norm.normalize(event, dev), path, dev.name)
            except OSError:
                log("detached %s (read error)" % path)
                try:
                    dev.close()
                except Exception:
                    pass
                devices.pop(path, None)
                nav.cursor_release()   # no drift from a half-deflected unplug

        nav.tick()


# ---------------------------------------------------------------------------
def selftest():
    """No-X unit test for the mapping + pause rule."""
    class E:
        def __init__(self, t, c, v):
            self.type, self.code, self.value = t, c, v

    failures = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    rec = []

    # HERMETIC nav factory: every environmental check (game / game-bar flag /
    # WM flag) is explicit, so a live VM's real /tmp flag files can't leak into
    # the test results (a stale /tmp/gose-gamebar-open broke 5 tests in-guest).
    def hnav(game=False, **kw):
        kw.setdefault("gamebar_check", lambda: False)
        kw.setdefault("wm_check", lambda: False)
        return PadNav(emit=rec.append, game_check=lambda: game, **kw)

    nav = hnav()

    # button maps
    check("BTN_TR -> bracketright",
          nav.map_event(E(ecodes.EV_KEY, ecodes.BTN_TR, 1)) == ["bracketright"])
    check("BTN_TL -> bracketleft",
          nav.map_event(E(ecodes.EV_KEY, ecodes.BTN_TL, 1)) == ["bracketleft"])
    check("BTN_SOUTH -> Return",
          nav.map_event(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1)) == ["Return"])
    check("BTN_EAST -> Escape",
          nav.map_event(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1)) == ["Escape"])
    check("BTN_START -> Return",
          nav.map_event(E(ecodes.EV_KEY, ecodes.BTN_START, 1)) == ["Return"])
    # release / autorepeat are ignored (debounce)
    check("BTN_TR release ignored",
          nav.map_event(E(ecodes.EV_KEY, ecodes.BTN_TR, 0)) == [])
    check("BTN_TR autorepeat(val2) ignored",
          nav.map_event(E(ecodes.EV_KEY, ecodes.BTN_TR, 2)) == [])

    # hat d-pad
    check("HAT0Y +1 -> Down",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_HAT0Y, 1)) == ["Down"])
    check("HAT0Y 0 (release) -> nothing",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_HAT0Y, 0)) == [])
    check("HAT0X -1 -> Left",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, -1)) == ["Left"])
    nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, 0))

    # the docs/27 §1.1 model change: stick axes are CURSOR axes, never keys
    check("stick ABS_X -> NO keysyms (cursor axis, not focus-nav)",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_X, 30000)) == [])
    check("stick ABS_Y -> NO keysyms (cursor axis, not focus-nav)",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_Y, -30000)) == [])
    nav.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 30000))      # pointer=None nav:
    check("stick deflection with no pointer engine -> safe no-op, no keys",
          rec == [] and nav.repeat == {})
    nav.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 0))

    # feed() emits via emit-fn when NOT paused
    rec.clear()
    nav.feed(E(ecodes.EV_KEY, ecodes.BTN_TR, 1))
    check("feed emits bracketright when no game", rec == ["bracketright"])

    # PAUSE rule: game running -> NO emission
    rec.clear()
    paused_nav = hnav(game=True)
    paused_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_TR, 1))
    check("PAUSE: game running suppresses emit", rec == [])
    # and resumes when game exits
    rec.clear()
    flag = {"g": True}
    resume_nav = PadNav(emit=rec.append, game_check=lambda: flag["g"],
                        gamebar_check=lambda: False, wm_check=lambda: False)
    resume_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))   # suppressed
    flag["g"] = False
    time.sleep(GAME_CACHE_S + 0.05)                          # let cache expire
    resume_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))   # now emits
    check("PAUSE: resumes after game exits", rec == ["Return"])

    # ----- GAME-BAR exception to the pause rule -----------------------------
    # game running BUT /tmp/gose-gamebar-open present -> NOT suppressed (drive bar)
    rec.clear()
    bar_nav = hnav(game=True, gamebar_check=lambda: True)
    bar_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))
    check("GAMEBAR: open during game -> emits (not suppressed)", rec == ["Return"])
    # game running and gamebar NOT open -> suppressed as before
    rec.clear()
    nobar_nav = hnav(game=True)
    nobar_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))
    check("GAMEBAR: closed during game -> suppressed", rec == [])

    # ----- WM MODAL LAYER (docs/23 §7, chunk B) ------------------------------
    import tempfile
    flagp = os.path.join(tempfile.gettempdir(), "test-gose-wm-open")
    try:
        os.remove(flagp)
    except Exception:
        pass
    posts = []

    def wm_pair(gate_fn=None, game=False):
        posts.clear(); rec.clear()
        wm = WMLayer(post=posts.append, flag_path=flagp,
                     gate=gate_fn or (lambda p, n=None: (True, "ok")))
        n = hnav(game=game, wm=wm, wm_check=lambda: os.path.exists(flagp))
        return n, wm

    DEV = "/dev/input/event20"
    # Guide DOWN -> carousel opens (event posted, flag dropped, NO key synthesized)
    n1, w1 = wm_pair()
    n1.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)
    check("WM: Guide down -> wm.carousel posted", posts == ["wm.carousel"])
    check("WM: Guide down -> flag file set", os.path.exists(flagp))
    check("WM: Guide down -> no key emitted", rec == [])
    # while held: R1/L1 cycle as semantic events, not bracket keys
    n1.feed(E(ecodes.EV_KEY, ecodes.BTN_TR, 1), DEV)
    check("WM: R1 in modal -> wm.next (no key)", posts[-1] == "wm.next" and rec == [])
    n1.feed(E(ecodes.EV_KEY, ecodes.BTN_TL, 1), DEV)
    check("WM: L1 in modal -> wm.prev", posts[-1] == "wm.prev")
    # d-pad cycles too
    n1.feed(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, 1), DEV)
    check("WM: d-pad right in modal -> wm.right", posts[-1] == "wm.right")
    n1.feed(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, 0), DEV)
    # Y -> overview, X -> act-out (stay in modal)
    n1.feed(E(ecodes.EV_KEY, ecodes.BTN_NORTH, 1), DEV)
    check("WM: Y in modal -> wm.overview", posts[-1] == "wm.overview")
    n1.feed(E(ecodes.EV_KEY, ecodes.BTN_WEST, 1), DEV)
    check("WM: X in modal -> wm.act (modal stays)", posts[-1] == "wm.act" and w1.mode == "carousel")
    # release after a HOLD -> selects + exits + flag removed
    w1.guide_t = time.time() - 1.0
    n1.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 0), DEV)
    check("WM: Guide release (held) -> wm.select + exit", posts[-1] == "wm.select" and w1.mode is None)
    check("WM: exit removes flag", not os.path.exists(flagp))
    # quick TAP -> sticky modal (no select on release); A then selects + exits
    n2, w2 = wm_pair()
    n2.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)
    n2.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 0), DEV)      # instant release = tap
    check("WM: Guide tap -> sticky (no select yet)", posts == ["wm.carousel"] and w2.mode == "carousel")
    n2.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("WM: A in sticky modal -> wm.select + exit", posts[-1] == "wm.select" and w2.mode is None)
    # B cancels
    n3, w3 = wm_pair()
    n3.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)
    n3.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)
    check("WM: B in modal -> wm.cancel + exit", posts[-1] == "wm.cancel" and w3.mode is None)
    check("WM: cancel removes flag", not os.path.exists(flagp))
    # normal nav is untouched when no modal is open
    n3.feed(E(ecodes.EV_KEY, ecodes.BTN_TR, 1), DEV)
    check("WM: normal mode still emits bracketright", rec == ["bracketright"])
    # L2 + d-pad -> snap chooser; A places but STAYS (assist); L2 release exits
    n4, w4 = wm_pair()
    n4.feed(E(ecodes.EV_ABS, ecodes.ABS_Z, 255), DEV)        # L2 held
    n4.feed(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, -1), DEV)     # + d-pad
    check("WM: L2+d-pad -> wm.snapmode", posts == ["wm.snapmode"] and w4.mode == "snap")
    check("WM: snap entry -> no arrow key leaked", rec == [])
    n4.feed(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, 0), DEV)
    n4.feed(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, -1), DEV)     # next press = direction
    check("WM: d-pad in snap modal -> wm.left", posts[-1] == "wm.left")
    n4.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("WM: A in snap modal -> wm.select, modal stays (assist)",
          posts[-1] == "wm.select" and w4.mode == "snap")
    n4.feed(E(ecodes.EV_ABS, ecodes.ABS_Z, 0), DEV)          # L2 released
    check("WM: L2 release exits snap modal + removes flag",
          w4.mode is None and not os.path.exists(flagp))
    # WM layer is admin-gated: a denied pad cannot open it
    n5, w5 = wm_pair(gate_fn=lambda p, n=None: (False, "not OS-admin"))
    n5.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)
    check("WM: gate-denied pad cannot enter the layer", posts == [] and w5.mode is None)
    # the /tmp/gose-wm-open exception: game running + flag -> keys NOT suppressed
    rec.clear()
    open(flagp, "w").close()
    nwm = hnav(game=True, wm_check=lambda: os.path.exists(flagp))
    nwm.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("WM-FLAG: open during game -> emits (mirror of Game-Bar exception)", rec == ["Return"])
    os.remove(flagp)
    rec.clear()
    nwm2 = hnav(game=True)
    nwm2.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("WM-FLAG: closed during game -> still suppressed", rec == [])

    # ----- NATIVE-APP DISCIPLINE (docs/27 §4.1: the browser-trap killer) -----
    # classification is pure: the kiosk family is NOT native; everything else is.
    check("NATIVE: kiosk window classified as kiosk-family",
          NativeWatch.classify(("kiosk.py", "Kiosk.py"), "GOSE") is True)
    check("NATIVE: overlay window classified as kiosk-family",
          NativeWatch.classify(("gose-overlay", "gose-overlay"), "GOSE Overlay") is True)
    check("NATIVE: Firefox classified as native",
          NativeWatch.classify(("Navigator", "firefox"), "Mozilla Firefox") is False)
    check("NATIVE: VLC classified as native",
          NativeWatch.classify(("vlc", "Vlc"), "VLC media player") is False)
    check("NATIVE: classless window with GOSE name still kiosk-family",
          NativeWatch.classify(None, "GOSE") is True)
    check("NATIVE: classless unknown window is native",
          NativeWatch.classify(None, "Something Else") is False)
    # decide(): UNKNOWN IS NEVER NATIVE (the 2026-06-07 kiosk-relaunch-loop bug:
    # a stale _NET_ACTIVE_WINDOW xid whose ident is unreadable must NOT become
    # B-closable — it was the kiosk itself)
    check("NATIVE: unreadable ident -> (unknown, NOT native) fail-safe",
          NativeWatch.decide(None, None) == (False, False))
    check("NATIVE: unreadable ident -> (unknown, NOT native) even with empty name",
          NativeWatch.decide(None, "") == (False, False))
    check("NATIVE: readable kiosk -> (known, not native)",
          NativeWatch.decide(("kiosk.py", "Kiosk.py"), "GOSE") == (True, False))
    check("NATIVE: readable app -> (known, native)",
          NativeWatch.decide(("Navigator", "firefox"), "Mozilla Firefox") == (True, True))
    check("NATIVE: name-only app -> (known, native)",
          NativeWatch.decide(None, "VLC media player") == (True, True))

    # maximize-once decision (no X needed: bare instance + the pure method)
    nwx = object.__new__(NativeWatch)
    nwx._seen = set()
    check("NATIVE: first native foreground + no game -> maximize",
          nwx.mark_foreground(0x500, True, False) == "maximize")
    check("NATIVE: same window again -> no re-maximize",
          nwx.mark_foreground(0x500, True, False) is None)
    check("NATIVE: kiosk-family window -> never maximized",
          nwx.mark_foreground(0x501, False, False) is None)
    check("NATIVE: native foreground DURING a game -> not maximized (game window)",
          nwx.mark_foreground(0x502, True, True) is None)
    check("NATIVE: xid 0 -> no-op", nwx.mark_foreground(0, True, False) is None)

    # feed() integration via a fake watcher (records actions, no X)
    class FakeNative:
        def __init__(self, native=True):
            self.native = native
            self.closed, self.raised = 0, 0

        def is_native_fg(self):
            return self.native

        def close_fg(self):
            self.closed += 1

        def activate_kiosk(self):
            self.raised += 1

    # B over a foreground native app -> POLITE CLOSE, no Escape synthesized
    rec.clear()
    fnat = FakeNative(True)
    nnav = hnav(native=fnat)
    nnav.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)
    check("NATIVE: B over native fg -> close_fg, no key", fnat.closed == 1 and rec == [])
    # B with the kiosk foreground -> plain Escape exactly as before
    rec.clear()
    fkio = FakeNative(False)
    knav = hnav(native=fkio)
    knav.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)
    check("NATIVE: B over kiosk fg -> Escape unchanged", rec == ["Escape"] and fkio.closed == 0)
    # B during a game -> suppressed entirely (game owns the pad; nothing closes)
    rec.clear()
    fgame = FakeNative(True)
    gnav2 = hnav(game=True, native=fgame)
    gnav2.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)
    check("NATIVE: B during game -> suppressed, window NOT closed",
          rec == [] and fgame.closed == 0)
    # other keys flow to the native app unchanged (A -> Return)
    rec.clear()
    fnat2 = FakeNative(True)
    onav = hnav(native=fnat2)
    onav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("NATIVE: A over native fg -> Return flows unchanged",
          rec == ["Return"] and fnat2.closed == 0)
    # Guide over native fg -> kiosk re-activated AND the carousel still opens
    posts.clear(); rec.clear()
    fng = FakeNative(True)
    wmg = WMLayer(post=posts.append, flag_path=flagp,
                  gate=lambda p, n=None: (True, "ok"))
    gnav3 = hnav(native=fng, wm=wmg, wm_check=lambda: os.path.exists(flagp))
    gnav3.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)
    check("NATIVE: Guide over native fg -> kiosk raised + wm.carousel",
          fng.raised == 1 and posts == ["wm.carousel"])
    gnav3.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)   # close the test modal
    # Guide with kiosk fg -> no raise (normal WM layer only)
    posts.clear()
    fng2 = FakeNative(False)
    wmg2 = WMLayer(post=posts.append, flag_path=flagp,
                   gate=lambda p, n=None: (True, "ok"))
    gnav4 = hnav(native=fng2, wm=wmg2, wm_check=lambda: os.path.exists(flagp))
    gnav4.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)
    check("NATIVE: Guide over kiosk fg -> no redundant raise",
          fng2.raised == 0 and posts == ["wm.carousel"])
    gnav4.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)
    # gate-denied pad cannot close a native window with B
    rec.clear()
    fden = FakeNative(True)
    dnav = hnav(native=fden, gate=lambda p, n=None: (False, "not OS-admin"))
    dnav.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)
    check("NATIVE: gate-denied pad's B does NOT close the native window",
          fden.closed == 0 and rec == [])
    # B inside a WM modal still cancels the modal (layer 3 beats native close)
    posts.clear()
    fmod = FakeNative(True)
    wmm = WMLayer(post=posts.append, flag_path=flagp,
                  gate=lambda p, n=None: (True, "ok"))
    mnav = hnav(native=fmod, wm=wmm, wm_check=lambda: os.path.exists(flagp))
    mnav.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)    # open modal (raises kiosk too)
    mnav.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)    # B = wm.cancel, NOT close_fg
    check("NATIVE: B in a WM modal -> wm.cancel, native window untouched",
          posts[-1] == "wm.cancel" and fmod.closed == 0)

    # ----- ADMIN GATE -------------------------------------------------------
    # Registry fixture: P1 native (admin candidate), a friend's BT pad, the dev
    # seat-1 virtual pad, and a 2nd virtual seat pad.
    PADS = [
        {"id": "input20", "name": "Xbox Wireless Controller", "source": "native",
         "path": "/dev/input/event20", "js": 0, "is_dev": False},
        {"id": "input22", "name": "8BitDo Pro 2", "source": "bluetooth",
         "path": "/dev/input/event22", "js": 1, "is_dev": False},
        {"id": "input5", "name": "AI virtual controller 1", "source": "virtual",
         "path": "/dev/input/event5", "js": 2, "is_dev": True},      # dev / seat 1
        {"id": "input6", "name": "AI virtual controller 2", "source": "virtual",
         "path": "/dev/input/event6", "js": 3, "is_dev": False},     # seat 2
    ]

    def gate(admin="input20", tokens=None, fetch=None, oobe_done=True):
        return AdminGate(refresh_s=0.0,
                         fetch_controllers=(fetch or (lambda: {"controllers": PADS, "admin": admin})),
                         read_admin_file=lambda: None,
                         read_ai_tokens=lambda: (tokens or {}),
                         read_oobe_done=lambda: oobe_done)

    g = gate()
    check("GATE: admin pad allowed",        g.allows("/dev/input/event20")[0] is True)
    check("GATE: non-admin (friend) ignored",
          g.allows("/dev/input/event22") == (False, "not OS-admin"))
    check("GATE: dev pad always allowed (even when not admin)",
          g.allows("/dev/input/event5")[0] is True)
    check("GATE: 2nd virtual seat ignored when no admin-AI seat grant",
          g.allows("/dev/input/event6")[0] is False)

    # fail-open: registry unreachable -> emit for all
    def boom():
        raise RuntimeError("server down")
    gfail = gate(fetch=boom)
    check("GATE: fail-open when registry unreachable",
          gfail.allows("/dev/input/event22")[0] is True)

    # unmapped device (not in registry) -> fail-open for that device
    check("GATE: unmapped device fail-open",
          gate().allows("/dev/input/event99")[0] is True)

    # admin-tier AI WITH a seat -> that seat's virtual pad (js order) allowed
    gseat = gate(tokens={"tok": {"name": "Agent-B", "tier": "admin", "seat": 2}})
    check("GATE: admin-AI seat 2 -> 2nd virtual pad allowed",
          gseat.allows("/dev/input/event6")[0] is True)
    check("GATE: admin-AI seat 2 does NOT open the friend's native pad",
          gseat.allows("/dev/input/event22")[0] is False)

    # admin-tier AI with NO seat -> drives via dev pad; does NOT open other virtuals
    gnoseat = gate(tokens={"tok": {"name": "Agent-A", "tier": "admin"}})
    check("GATE: no-seat admin-AI grant does NOT open 2nd virtual seat",
          gnoseat.allows("/dev/input/event6")[0] is False)

    # seated admin-AI but seat out of range -> best-effort allow-all-virtual
    goob = gate(tokens={"tok": {"name": "Agent-B", "tier": "admin", "seat": 9}})
    check("GATE: unmappable admin-AI seat -> all-virtual fallback (input6 allowed)",
          goob.allows("/dev/input/event6")[0] is True)
    check("GATE: all-virtual fallback still ignores native friend pad",
          goob.allows("/dev/input/event22")[0] is False)

    # docs/25 §5.2b: PRE-USER first boot (no admin set + OOBE not done) -> ANY pad drives the wizard
    gpre = gate(admin=None, oobe_done=False)
    check("GATE: first-boot (no admin, OOBE not done) -> friend pad allowed",
          gpre.allows("/dev/input/event22")[0] is True)
    check("GATE: first-boot reason is the wizard exception",
          "first-boot" in gpre.allows("/dev/input/event22")[1])
    check("GATE: first-boot -> even an unmapped pad drives the wizard",
          gpre.allows("/dev/input/event99")[0] is True)
    # once setup is DONE, a missing admin no longer opens the OS to a random pad
    gdone = gate(admin=None, oobe_done=True)
    check("GATE: setup done + no admin -> friend pad denied (no longer pre-user)",
          gdone.allows("/dev/input/event22")[0] is False)
    # admin already chosen -> normal arbitration even before OOBE flag is written
    gadmin_pre = gate(admin="input20", oobe_done=False)
    check("GATE: admin set during OOBE -> non-admin friend still denied",
          gadmin_pre.allows("/dev/input/event22")[0] is False)
    check("GATE: admin set during OOBE -> admin pad allowed",
          gadmin_pre.allows("/dev/input/event20")[0] is True)

    # gate wired into feed(): non-admin pad's button does not emit
    rec.clear()
    fnav = hnav(gate=gate().allows)
    fnav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), "/dev/input/event22", "8BitDo Pro 2")
    check("GATE: feed() suppresses non-admin button", rec == [])
    rec.clear()
    fnav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), "/dev/input/event20", "Xbox Wireless Controller")
    check("GATE: feed() emits for admin button", rec == ["Return"])

    # ----- STICK CURSOR (docs/27 §1.1: d-pad = focus, left stick = pointer) --
    class FakePtr:
        can_hide = True

        def __init__(self):
            self.moves, self.clicks, self.vis = [], [], []
            self.pos = (100, 100)       # the fake X pointer position
            self.buttons = False        # any pointer button held

        def move(self, dx, dy):
            self.moves.append((dx, dy))
            self.pos = (self.pos[0] + dx, self.pos[1] + dy)

        def click(self, b=1):
            self.clicks.append(b)

        def hide_cursor(self):
            self.vis.append("hide")

        def show_cursor(self):
            self.vis.append("show")

        def pointer_pos(self):
            return self.pos

        def pointer_state(self):
            return (self.pos, self.buttons)

    def cnav(game=False, gate_fn=None, wm=None):
        p = FakePtr()
        n = hnav(game=game, gate=gate_fn, wm=wm, pointer=p)
        return n, p

    def mtick(n, dt):
        """Deterministic cursor tick: pretend the last tick was dt seconds ago."""
        n._cursor_t = time.time() - dt
        n.tick()

    rec.clear()
    cn, cp = cnav()
    cn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    check("CURSOR: deflection queues NO key auto-repeat", cn.repeat == {})
    check("CURSOR: deflection emits NO keys", rec == [])
    mtick(cn, 0.1)
    check("CURSOR: deflection -> pointer motion", len(cp.moves) == 1)
    check("CURSOR: full-right deflection -> +x at max speed (90px in 0.1s)",
          cp.moves[0] == (90, 0))
    for _ in range(9):
        mtick(cn, 0.1)                      # 1.0s total at full deflection
    total = sum(dx for dx, _ in cp.moves)
    check("CURSOR: linear accel tops out at ~%dpx/s" % int(CURSOR_MAX_SPEED),
          abs(total - CURSOR_MAX_SPEED) <= 1)
    cn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 0), DEV)       # re-center
    cp.moves.clear()
    mtick(cn, 0.1)
    check("CURSOR: re-center stops motion", cp.moves == [])
    cn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 5000), DEV)    # inside deadzone
    mtick(cn, 0.1)
    check("CURSOR: deflection inside deadzone -> no motion", cp.moves == [])
    cn.feed(E(ecodes.EV_ABS, ecodes.ABS_Y, 32767), DEV)   # stick down
    mtick(cn, 0.1)
    check("CURSOR: stick down -> +y motion", cp.moves and cp.moves[-1][1] > 0)
    cn.feed(E(ecodes.EV_ABS, ecodes.ABS_Y, 0), DEV)

    # A clicks ONLY while the cursor is active (moved within CURSOR_CLICK_WINDOW)
    rec.clear()
    check("CURSOR: pointer just moved -> cursor_active", cn.cursor_active())
    cn.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("CURSOR: A at active cursor -> CLICK, not Return",
          cp.clicks == [1] and rec == [])
    cn.feed(E(ecodes.EV_KEY, ecodes.BTN_START, 1), DEV)
    check("CURSOR: Start stays Return even at active cursor", rec == ["Return"])
    rec.clear()
    cp.clicks.clear()
    cn.cursor_last_move = time.time() - (CURSOR_CLICK_WINDOW + 0.2)
    cn.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("CURSOR: A at stale cursor -> Return (focus-nav untouched)",
          rec == ["Return"] and cp.clicks == [])

    # suppression covers the cursor exactly like keys (game owns the pad)
    rec.clear()
    sn, sp = cnav(game=True)
    sn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    mtick(sn, 0.1)
    check("CURSOR: game running suppresses pointer motion", sp.moves == [])
    sn.cursor_last_move = time.time()      # even an 'active' cursor can't click
    sn.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), DEV)
    check("CURSOR: game running suppresses A-click too",
          sp.clicks == [] and rec == [])

    # the WM modal layer owns the whole pad -> stick motion is frozen
    rec.clear()
    wmw = WMLayer(post=posts.append, flag_path=flagp,
                  gate=lambda p, n=None: (True, "ok"))
    wn, wp = cnav(wm=wmw)
    wn.feed(E(ecodes.EV_KEY, ecodes.BTN_MODE, 1), DEV)     # carousel opens
    wn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    mtick(wn, 0.1)
    check("CURSOR: WM modal open -> stick does NOT move pointer", wp.moves == [])
    wn.feed(E(ecodes.EV_KEY, ecodes.BTN_EAST, 1), DEV)     # B cancels the modal
    mtick(wn, 0.1)
    check("CURSOR: modal closed -> held deflection resumes motion",
          len(wp.moves) > 0)

    # auto-hide after idle + reappear on stick motion (XFixes via the engine)
    hn, hp = cnav()
    hn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    mtick(hn, 0.1)
    check("CURSOR: visible while in use", hn.cursor_visible)
    hn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 0), DEV)
    hn.cursor_last_move = time.time() - (CURSOR_HIDE_S + 1.0)
    mtick(hn, 0.016)
    check("CURSOR: auto-hides after %.0fs idle" % CURSOR_HIDE_S,
          hp.vis[-1:] == ["hide"] and not hn.cursor_visible)
    hn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    mtick(hn, 0.1)
    check("CURSOR: stick motion re-shows the cursor",
          "show" in hp.vis and hn.cursor_visible and len(hp.moves) > 0)

    # ----- EXTERNAL POINTER WAKE (task 26: any pointer motion wakes it) ------
    # a REAL mouse moving while the cursor is hidden -> SHOW + idle-timer reset
    en, ep = cnav()
    en.cursor_visible = False
    mtick(en, 0.016)                              # tick 1: baselines ep.pos
    ep.pos = (ep.pos[0] + 25, ep.pos[1])          # an external (non-bridge) move
    en.cursor_last_move = time.time() - (CURSOR_HIDE_S + 2.0)
    mtick(en, 0.016)                              # tick 2: detects + wakes
    check("EXT: external motion while hidden -> SHOW",
          ep.vis[-1:] == ["show"] and en.cursor_visible)
    check("EXT: external motion resets the idle timer (no instant re-hide)",
          time.time() - en.cursor_last_move < 1.0)
    # our own XTEST motion must NOT read as external (else: never hides)
    xn, xp = cnav()
    xn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    mtick(xn, 0.1)                                # bridge self-moves the pointer
    xn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 0), DEV)
    mtick(xn, 0.016)                              # quiet tick: re-baselines only
    xp.vis.clear()
    xn.cursor_last_move = time.time() - (CURSOR_HIDE_S + 1.0)
    mtick(xn, 0.016)                              # no wake -> idle hide fires
    check("EXT: self (XTEST) motion is not external -> idle hide still fires",
          xp.vis[-1:] == ["hide"] and not xn.cursor_visible)
    # a pressed pointer button wakes the hidden cursor (failsafe)
    bn, bp = cnav()
    mtick(bn, 0.016)                              # baseline
    bn.cursor_visible = False
    bp.buttons = True                             # a held real mouse button
    mtick(bn, 0.016)
    check("EXT: pointer button while hidden -> SHOW",
          bp.vis[-1:] == ["show"] and bn.cursor_visible)
    # suppression respected: external motion during a game must NOT wake
    egn, egp = cnav(game=True)
    egn.cursor_visible = False
    mtick(egn, 0.016)
    egp.pos = (600, 500)
    mtick(egn, 0.016)
    check("EXT: external motion during a game -> suppressed (no SHOW)",
          egp.vis == [] and not egn.cursor_visible)
    # ...and motion accumulated DURING the game can't phantom-SHOW at game exit
    eflag = {"g": True}
    epp = FakePtr()
    epn = PadNav(emit=rec.append, game_check=lambda: eflag["g"],
                 gamebar_check=lambda: False, wm_check=lambda: False,
                 pointer=epp)
    epn.cursor_visible = False
    mtick(epn, 0.016)                             # blocked: baseline stays dead
    epp.pos = (50, 50)                            # mouse moved during the game
    eflag["g"] = False
    time.sleep(GAME_CACHE_S + 0.05)               # let the game cache expire
    mtick(epn, 0.016)                             # first free tick: re-baseline
    check("EXT: in-game motion does not phantom-SHOW at game exit",
          epp.vis == [] and not epn.cursor_visible)
    epp.pos = (60, 50)                            # a FRESH external move
    mtick(epn, 0.016)
    check("EXT: after re-baseline a new external move shows again",
          epp.vis[-1:] == ["show"] and epn.cursor_visible)

    # GameWatch: the off-hot-path game check (refresh() is the testable core)
    gw = GameWatch(check=lambda: True)
    check("GAMEWATCH: refresh propagates True", gw.refresh() is True and gw.is_running())
    gw2 = GameWatch(check=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    gw2.value = True
    check("GAMEWATCH: a crashing check fails SAFE to False (no stuck suppression)",
          gw2.refresh() is False and not gw2.is_running())

    # admin gate covers the stick (a denied pad cannot drive the pointer)
    gn, gp = cnav(gate_fn=lambda p, n=None: (False, "not OS-admin"))
    gn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    mtick(gn, 0.1)
    check("CURSOR: gate-denied pad's stick does not move pointer",
          gp.moves == [])

    # d-pad repeat machinery is intact; the stick contributes no repeat state
    rec.clear()
    rn, rp = cnav()
    rn.feed(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, 1), DEV)
    check("CURSOR: d-pad still queues auto-repeat", ecodes.ABS_HAT0X in rn.repeat)
    rn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 32767), DEV)
    check("CURSOR: stick never queues auto-repeat", ecodes.ABS_X not in rn.repeat)
    rn.feed(E(ecodes.EV_ABS, ecodes.ABS_HAT0X, 0), DEV)
    # select() pacing: 16ms while deflected; default cadence when fully idle
    check("CURSOR: next_timeout is %.0fms while deflected" % (CURSOR_TICK_S * 1000),
          rn.next_timeout(2.0) <= CURSOR_TICK_S)
    rn.feed(E(ecodes.EV_ABS, ecodes.ABS_X, 0), DEV)
    rn.cursor_visible = False                       # idle + already hidden
    check("CURSOR: next_timeout returns to default at idle (no busy-spin)",
          rn.next_timeout(2.0) == 2.0)

    # ----- UNIVERSAL NORMALIZATION (SDL_GameControllerDB) --------------------
    # SDL GUID computation matches the DB row format.
    check("GUID: Xbox 360 045e:028e bus3 v0110",
          _sdl_guid(3, 0x045e, 0x028e, 0x0110) == "030000005e0400008e02000010010000")
    check("GUID: DualSense 054c:0ce6 bus3 v8111 (hid-playstation)",
          _sdl_guid(3, 0x054c, 0x0ce6, 0x8111) == "030000004c050000e60c000011810000")
    check("GUID: zero-crc fold leaves classic rows intact",
          _guid_zero_crc("030000005e0400008e02000010010000")
          == "030000005e0400008e02000010010000")

    # A tiny DB fixture exercises parse + lookup independent of the deployed file.
    FIX = "\n".join([
        "030000005e0400008e02000010010000,Xbox 360,a:b0,b:b1,x:b2,y:b3,"
        "leftshoulder:b4,rightshoulder:b5,start:b7,back:b6,guide:b8,"
        "dpup:h0.1,leftx:a0,lefty:a1,platform:Linux,",
        # DualSense, modern hid-playstation variant -> position-standard a:b0
        "030000004c050000e60c000011810000,PS5 Controller,a:b0,b:b1,x:b3,y:b2,"
        "leftshoulder:b4,rightshoulder:b5,start:b9,back:b8,guide:b10,"
        "dpup:h0.1,leftx:a0,lefty:a1,lefttrigger:a2,platform:Linux,",
        # A generic/DInput pad that does NOT report BTN_SOUTH -> needs remap.
        # GUID = _sdl_guid(3, 0x1234, 0x5678, 0x0001) (vendor/product little-endian).
        "03000000341200007856000001000000,Generic DInput Pad,a:b0,b:b1,x:b3,y:b2,"
        "leftshoulder:b4,rightshoulder:b5,start:b9,back:b8,guide:b10,"
        "dpup:h0.1,leftx:a0,lefty:a1,platform:Linux,",
        "deadbeef,broken,platform:Windows,",   # ignored (not Linux / bad guid)
    ])
    fdb = ControllerDB(text=FIX)
    check("DB: parses Linux rows only", fdb.count == 3)
    check("DB: DualSense found by GUID (PS5 0ce6)",
          fdb.entry_for(3, 0x054c, 0x0ce6, 0x8111) is not None)
    check("DB: DualSense Cross == standard A (a:b0)",
          fdb.entry_for(3, 0x054c, 0x0ce6, 0x8111).get("a") == ("b", 0))
    check("DB: Xbox 360 found by GUID",
          fdb.entry_for(3, 0x045e, 0x028e, 0x0110) is not None)
    check("DB: unknown GUID -> None",
          fdb.entry_for(3, 0xDEAD, 0xBEEF, 0x0000) is None)

    # SDL index enumeration (Linux): [BTN_JOYSTICK..) then [BTN_MISC..BTN_JOYSTICK)
    bmap = _sdl_button_index_map({ecodes.BTN_SOUTH, ecodes.BTN_EAST,
                                  ecodes.BTN_NORTH, ecodes.BTN_WEST})
    check("ENUM: BTN_SOUTH is b0 in a contiguous standard set",
          bmap[ecodes.BTN_SOUTH] == 0 and bmap[ecodes.BTN_EAST] == 1)

    # Position-standard pad (has BTN_SOUTH) -> IDENTITY remap (zero regression).
    std_keys = {ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_NORTH,
                ecodes.BTN_WEST, ecodes.BTN_TL, ecodes.BTN_TR,
                ecodes.BTN_START, ecodes.BTN_SELECT, ecodes.BTN_MODE}
    std_abs = {ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y}
    check("NORM: kernel-standard pad (virtual Xbox/DS4/DualSense) -> identity",
          Normalizer.build_remap(fdb, 3, 0x045e, 0x028e, 0x0110,
                                 std_keys, std_abs) == {})
    check("NORM: DualSense (modern, BTN_SOUTH present) -> identity too",
          Normalizer.build_remap(fdb, 3, 0x054c, 0x0ce6, 0x8111,
                                 std_keys, std_abs) == {})

    # Generic pad with NO BTN_SOUTH: buttons live at BTN_TRIGGER(0x120)+ ->
    # build a remap that rewrites them to the standard face-button codes.
    gen_keys = set(range(ecodes.BTN_TRIGGER, ecodes.BTN_TRIGGER + 10))  # 0x120..0x129
    gen_abs = {ecodes.ABS_X, ecodes.ABS_Y}
    gremap = Normalizer.build_remap(fdb, 3, 0x1234, 0x5678, 0x0001,
                                    gen_keys, gen_abs)
    # a:b0 -> raw BTN_TRIGGER(0x120) must remap to BTN_SOUTH; b:b1 -> BTN_EAST
    check("NORM: generic pad A(b0) remaps to BTN_SOUTH",
          gremap.get((ecodes.EV_KEY, ecodes.BTN_TRIGGER)) == ecodes.BTN_SOUTH)
    check("NORM: generic pad B(b1) remaps to BTN_EAST",
          gremap.get((ecodes.EV_KEY, ecodes.BTN_TRIGGER + 1)) == ecodes.BTN_EAST)
    # ...and after that remap the SAME map_event yields the SAME keysym as Xbox A.
    nrec = PadNav(emit=None, game_check=lambda: False)
    check("NORM: remapped generic A -> Return (same language as Xbox/DualSense)",
          nrec.map_event(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1)) == ["Return"])

    # Unknown non-standard pad -> identity (sane defaults, never crashes).
    check("NORM: unknown non-standard pad -> identity",
          Normalizer.build_remap(fdb, 3, 0xAAAA, 0xBBBB, 0x1, gen_keys, gen_abs) == {})

    # The new discrete d-pad buttons navigate (for button-d-pad pads).
    check("NORM: BTN_DPAD_UP -> Up", nrec.map_event(E(ecodes.EV_KEY, ecodes.BTN_DPAD_UP, 1)) == ["Up"])
    check("NORM: BTN_DPAD_RIGHT -> Right", nrec.map_event(E(ecodes.EV_KEY, ecodes.BTN_DPAD_RIGHT, 1)) == ["Right"])

    # ----- DEVICE FILTER (is_gamepad) ----------------------------------------
    # Composite-pad sibling nodes must be REJECTED even though their names say
    # "Controller" (the 2026-06-07 7.2s-lag bug: the motion node's accelerometer
    # emitted phantom arrows that queued real presses behind xdotool spawns).
    class FakeDev:
        def __init__(self, name, keys=(), info=(3, 0, 0, 0)):
            self.name = name
            self._keys = list(keys)
            self.info = type("I", (), dict(zip(
                ("bustype", "vendor", "product", "version"), info)))()

        def capabilities(self):
            return {ecodes.EV_KEY: self._keys} if self._keys else {}

    DS = "Sony Interactive Entertainment DualSense Wireless Controller"
    check("FILTER: main pad (BTN_SOUTH..) accepted",
          is_gamepad(FakeDev(DS, [ecodes.BTN_SOUTH, ecodes.BTN_EAST,
                                  ecodes.BTN_TL, ecodes.BTN_MODE])))
    check("FILTER: virtual AI pad accepted",
          is_gamepad(FakeDev("AI virtual controller 1",
                             [ecodes.BTN_SOUTH, ecodes.BTN_START])))
    check("FILTER: generic DInput pad (BTN_TRIGGER range) accepted",
          is_gamepad(FakeDev("USB Joystick", list(range(ecodes.BTN_TRIGGER,
                                                        ecodes.BTN_TRIGGER + 10)))))
    check("FILTER: button-d-pad-only pad accepted",
          is_gamepad(FakeDev("Mini Pad", [ecodes.BTN_DPAD_UP, ecodes.BTN_DPAD_DOWN])))
    check("FILTER: Motion Sensors node (no EV_KEY) REJECTED despite name",
          not is_gamepad(FakeDev(DS + " Motion Sensors")))
    check("FILTER: Touchpad node (mouse/digitizer buttons) REJECTED despite name",
          not is_gamepad(FakeDev(DS + " Touchpad",
                                 [ecodes.BTN_LEFT, ecodes.BTN_TOUCH,
                                  ecodes.BTN_TOOL_FINGER, ecodes.BTN_TOOL_DOUBLETAP])))
    check("FILTER: Headset Jack node (no buttons) REJECTED despite name",
          not is_gamepad(FakeDev(DS + " Headset Jack")))
    check("FILTER: keyboard (KEY_* < BTN_MISC) REJECTED",
          not is_gamepad(FakeDev("AT Translated Keyboard",
                                 [ecodes.KEY_A, ecodes.KEY_ENTER])))

    print("\n%d test(s) FAILED" % len(failures) if failures else "\nALL TESTS PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run()
