#!/usr/bin/env python3
"""GOSE gamepad -> keyboard bridge.

WebKit2GTK in the GOSE kiosk has no gamepad library (libmanette absent) and no
gamepad->key mapper (evmapy/gptokeyb absent), so the controller cannot drive the
controller-first UI on its own. BUT the UI's navigation is keyboard-based and
verified working (arrows move focus, [ / ] switch store tabs, Enter activates,
Esc -> desktop). This daemon reads the controller via python-evdev and synthesizes
the matching X key events with xdotool -- making the whole UI controller-driven.

PAUSE rule: when a game/emulator (retroarch / emulatorlauncher) is foreground, the
pad belongs to the game (RetroArch reads evdev directly); we must NOT emit phantom
keys. We detect that and go silent until the game exits.

Injection path (PROVEN): `DISPLAY=:0 xdotool key <keysym>` drives the kiosk; X was
started without -auth so no XAUTHORITY is needed.

Config (device match + keymaps) is in editable dicts at the top of this file.
The mapping is pure/unit-testable via PadNav.map_event (no X required) -- see
the __main__ self-test (`gose-pad-nav.py --selftest`).
"""
import os
import sys
import time
import glob
import select
import subprocess

import evdev
from evdev import ecodes

# ---------------------------------------------------------------------------
# CONFIG  (edit here)
# ---------------------------------------------------------------------------

# A device is a gamepad if its name contains any of these (case-insensitive)...
NAME_KEYWORDS = ("pad", "controller", "xbox", "gamepad", "joystick")
# ...OR it advertises one of these EV_KEY capabilities (a real gamepad does).
GAMEPAD_KEY_CAPS = (ecodes.BTN_GAMEPAD, ecodes.BTN_SOUTH)  # BTN_GAMEPAD == BTN_SOUTH (304)

# Button (EV_KEY) -> X keysym.  Only key-DOWN (value==1) fires; autorepeat
# (value==2) and release (value==0) are ignored -> debounces button mash.
BUTTON_KEYMAP = {
    ecodes.BTN_SOUTH:  "Return",        # A
    ecodes.BTN_EAST:   "Escape",        # B
    ecodes.BTN_TL:     "bracketleft",   # L1  -> prev store tab
    ecodes.BTN_TR:     "bracketright",  # R1  -> next store tab
    ecodes.BTN_START:  "Return",        # Start (also activates)
    ecodes.BTN_SELECT: "Escape",        # Select (optional back)
}

# Axis (EV_ABS) -> directional keysyms.  'stick' axes use a deadzone around
# center 0; hats are ternary (-1 / 0 / +1).  Held directions auto-repeat.
AXIS_MAP = {
    ecodes.ABS_HAT0X: {"neg": "Left", "pos": "Right"},
    ecodes.ABS_HAT0Y: {"neg": "Up",   "pos": "Down"},
    ecodes.ABS_X:     {"neg": "Left", "pos": "Right", "stick": True},
    ecodes.ABS_Y:     {"neg": "Up",   "pos": "Down",  "stick": True},
}

STICK_DEADZONE = 12000   # |value| past this (from center 0) counts as a press
REPEAT_INITIAL = 0.40    # s before a held direction starts repeating
REPEAT_INTERVAL = 0.18   # s between repeats while held (~180ms)
GAME_CACHE_S = 0.30      # cache the "is a game running" check this long
DEVICE_RESCAN_S = 2.0    # poll /dev/input for hotplug this often

LOGFILE = "/userdata/system/logs/gose-pad-nav.log"

# Process names that mean "a game/emulator owns the pad -> stay silent".
GAME_PGREP_EXACT = ("retroarch",)            # pgrep -x
GAME_PGREP_FULL = ("emulatorlauncher",)      # pgrep -f


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


def default_game_running():
    """True if a game/emulator is foreground (pad belongs to it)."""
    for name in GAME_PGREP_EXACT:
        if subprocess.call(["pgrep", "-x", name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            return True
    for name in GAME_PGREP_FULL:
        if subprocess.call(["pgrep", "-f", name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            return True
    return False


def xdotool_emit(keysym):
    env = dict(os.environ, DISPLAY=":0")
    subprocess.call(["xdotool", "key", keysym], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_gamepad(dev):
    name = (dev.name or "").lower()
    if any(k in name for k in NAME_KEYWORDS):
        return True
    try:
        keys = dev.capabilities().get(ecodes.EV_KEY, [])
    except Exception:
        keys = []
    return any(c in keys for c in GAMEPAD_KEY_CAPS)


# ---------------------------------------------------------------------------
class PadNav:
    """Maps controller events to keysyms and emits them via `emit`, unless a
    game is running (`game_check`).  map_event() is pure (no X) -> unit-testable.
    """

    def __init__(self, emit=None, game_check=None, deadzone=STICK_DEADZONE):
        self.emit = emit or xdotool_emit
        self.game_check = game_check or default_game_running
        self.deadzone = deadzone
        self.axis_dirs = {}    # ecode -> "neg"/"pos"/None  (current crossed dir)
        self.repeat = {}       # ecode -> {"key": keysym, "next": float}
        self._game_cache = (0.0, False)

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

    # --- dispatch (applies pause + emits) -----------------------------------
    def _dispatch(self, keysyms):
        if not keysyms:
            return
        if self.is_paused():
            for k in keysyms:
                log("PAUSED (game running) -> suppressed %s" % k)
            return
        for k in keysyms:
            self.emit(k)
            log("emit key %s" % k)

    def feed(self, event):
        self._dispatch(self.map_event(event))

    def tick(self):
        """Auto-repeat held directions. Call frequently (e.g. on select timeout)."""
        now = time.time()
        due = []
        for ec, st in self.repeat.items():
            if now >= st["next"]:
                due.append(st["key"])
                st["next"] = now + REPEAT_INTERVAL
        self._dispatch(due)

    def next_repeat_timeout(self, default):
        if not self.repeat:
            return default
        now = time.time()
        soonest = min(st["next"] for st in self.repeat.values())
        return max(0.0, min(default, soonest - now))


# ---------------------------------------------------------------------------
def run():
    log("gose-pad-nav starting (pid %d)" % os.getpid())
    nav = PadNav()
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
            if is_gamepad(dev):
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
        timeout = nav.next_repeat_timeout(DEVICE_RESCAN_S)
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
            continue

        for fd in r:
            path = fds[fd]
            dev = devices.get(path)
            if dev is None:
                continue
            try:
                for event in dev.read():
                    if event.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                        nav.feed(event)
            except OSError:
                log("detached %s (read error)" % path)
                try:
                    dev.close()
                except Exception:
                    pass
                devices.pop(path, None)

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
    nav = PadNav(emit=rec.append, game_check=lambda: False)

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

    # stick deadzone: small value = nothing, big = arrow, once per cross
    check("stick ABS_X small (in deadzone) -> nothing",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_X, 5000)) == [])
    check("stick ABS_X full right -> Right",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_X, 30000)) == ["Right"])
    check("stick ABS_X still right -> no re-fire",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_X, 31000)) == [])
    check("stick ABS_X back to center -> nothing",
          nav.map_event(E(ecodes.EV_ABS, ecodes.ABS_X, 0)) == [])

    # feed() emits via emit-fn when NOT paused
    rec.clear()
    nav.feed(E(ecodes.EV_KEY, ecodes.BTN_TR, 1))
    check("feed emits bracketright when no game", rec == ["bracketright"])

    # PAUSE rule: game running -> NO emission
    rec.clear()
    paused_nav = PadNav(emit=rec.append, game_check=lambda: True)
    paused_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_TR, 1))
    check("PAUSE: game running suppresses emit", rec == [])
    # and resumes when game exits
    rec.clear()
    flag = {"g": True}
    resume_nav = PadNav(emit=rec.append, game_check=lambda: flag["g"])
    resume_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))   # suppressed
    flag["g"] = False
    time.sleep(GAME_CACHE_S + 0.05)                          # let cache expire
    resume_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))   # now emits
    check("PAUSE: resumes after game exits", rec == ["Return"])

    print("\n%d test(s) FAILED" % len(failures) if failures else "\nALL TESTS PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run()
