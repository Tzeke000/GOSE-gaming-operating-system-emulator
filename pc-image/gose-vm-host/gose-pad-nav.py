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
import json
import select
import subprocess
import urllib.request

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

# --- OS-control arbitration (admin gating) ---------------------------------
# Only the OS-admin / Player-1 controller (plus the dev pad + an admin-tier AI's
# seat) may drive the OS menus.  A friend's pad does nothing in menus but still
# works in games (games read evdev directly; this daemon is silent then anyway).
SERVER_URL = "http://127.0.0.1:8780"             # in-guest GOSE server (controller registry)
OS_ADMIN_FILE = "/userdata/system/gose/os_admin_controller.json"  # admin-id fallback source
AI_TOKENS_FILE = os.environ.get("GOSE_AGENT_AI_TOKENS",
                                "/userdata/system/gose/ai_tokens.json")  # token->{name,tier[,seat]}
GATE_REFRESH_S = 3.0                              # re-read registry/admin/tokens this often

# Game Bar overlay drops this file while it is open.  When present we do NOT
# suppress emits even though a game process is running, so the pad can drive the
# bar.  Removing the file restores the normal "game owns the pad" silence.
GAMEBAR_FLAG = "/tmp/gose-gamebar-open"


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


def xdotool_emit(keysym):
    env = dict(os.environ, DISPLAY=":0")
    subprocess.call(["xdotool", "key", keysym], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
      order (all AI seat pads share the uinput name "Microsoft Xbox 360 pad", so
      js order is the only discriminator -- this ordering matches the agent's
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
                 fetch_controllers=None, read_admin_file=None, read_ai_tokens=None):
        self.refresh_s = refresh_s
        self._fetch_controllers = fetch_controllers or self._default_fetch
        self._read_admin_file = read_admin_file or self._default_admin_file
        self._read_ai_tokens = read_ai_tokens or self._default_ai_tokens
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

        self._state = {
            "admin_id": admin_id,
            "dev_ids": dev_ids,
            "ai_seat_ids": ai_seat_ids,
            "allow_all_virtual": allow_all_virtual,
            "ai_admin": ai_admin,
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

    def __init__(self, emit=None, game_check=None, deadzone=STICK_DEADZONE,
                 gate=None, gamebar_check=None):
        self.emit = emit or xdotool_emit
        self.game_check = game_check or default_game_running
        self.gamebar_check = gamebar_check or default_gamebar_open
        # gate(dev_path, dev_name) -> (allowed, reason).  Default: allow all
        # (so unit tests / un-wired use don't gate); run() wires AdminGate.allows.
        self.gate = gate or (lambda path, name=None: (True, "no-gate"))
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

    def is_suppressed(self):
        """Emits are suppressed only when a game runs AND the Game Bar is NOT open.
        The Game Bar overlay (its flag file) is the exception that lets the pad
        drive the bar even while the game process is alive."""
        if not self.is_paused():
            return False
        try:
            if self.gamebar_check():
                return False        # game bar open -> drive the bar, don't suppress
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
        for k in keysyms:
            self.emit(k)
            log("emit key %s" % k)

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
    nav = PadNav(gate=AdminGate().allows)
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
                        nav.feed(event, path, dev.name)
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

    # ----- GAME-BAR exception to the pause rule -----------------------------
    # game running BUT /tmp/gose-gamebar-open present -> NOT suppressed (drive bar)
    rec.clear()
    bar_nav = PadNav(emit=rec.append, game_check=lambda: True, gamebar_check=lambda: True)
    bar_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))
    check("GAMEBAR: open during game -> emits (not suppressed)", rec == ["Return"])
    # game running and gamebar NOT open -> suppressed as before
    rec.clear()
    nobar_nav = PadNav(emit=rec.append, game_check=lambda: True, gamebar_check=lambda: False)
    nobar_nav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1))
    check("GAMEBAR: closed during game -> suppressed", rec == [])

    # ----- ADMIN GATE -------------------------------------------------------
    # Registry fixture: P1 native (admin candidate), a friend's BT pad, the dev
    # seat-1 virtual pad, and a 2nd virtual seat pad.
    PADS = [
        {"id": "input20", "name": "Xbox Wireless Controller", "source": "native",
         "path": "/dev/input/event20", "js": 0, "is_dev": False},
        {"id": "input22", "name": "8BitDo Pro 2", "source": "bluetooth",
         "path": "/dev/input/event22", "js": 1, "is_dev": False},
        {"id": "input5", "name": "Microsoft Xbox 360 pad", "source": "virtual",
         "path": "/dev/input/event5", "js": 2, "is_dev": True},      # dev / seat 1
        {"id": "input6", "name": "Microsoft Xbox 360 pad", "source": "virtual",
         "path": "/dev/input/event6", "js": 3, "is_dev": False},     # seat 2
    ]

    def gate(admin="input20", tokens=None, fetch=None):
        return AdminGate(refresh_s=0.0,
                         fetch_controllers=(fetch or (lambda: {"controllers": PADS, "admin": admin})),
                         read_admin_file=lambda: None,
                         read_ai_tokens=lambda: (tokens or {}))

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
    gseat = gate(tokens={"tok": {"name": "Iris", "tier": "admin", "seat": 2}})
    check("GATE: admin-AI seat 2 -> 2nd virtual pad allowed",
          gseat.allows("/dev/input/event6")[0] is True)
    check("GATE: admin-AI seat 2 does NOT open the friend's native pad",
          gseat.allows("/dev/input/event22")[0] is False)

    # admin-tier AI with NO seat -> drives via dev pad; does NOT open other virtuals
    gnoseat = gate(tokens={"tok": {"name": "Wren", "tier": "admin"}})
    check("GATE: no-seat admin-AI grant does NOT open 2nd virtual seat",
          gnoseat.allows("/dev/input/event6")[0] is False)

    # seated admin-AI but seat out of range -> best-effort allow-all-virtual
    goob = gate(tokens={"tok": {"name": "Iris", "tier": "admin", "seat": 9}})
    check("GATE: unmappable admin-AI seat -> all-virtual fallback (input6 allowed)",
          goob.allows("/dev/input/event6")[0] is True)
    check("GATE: all-virtual fallback still ignores native friend pad",
          goob.allows("/dev/input/event22")[0] is False)

    # gate wired into feed(): non-admin pad's button does not emit
    rec.clear()
    fnav = PadNav(emit=rec.append, game_check=lambda: False, gate=gate().allows)
    fnav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), "/dev/input/event22", "8BitDo Pro 2")
    check("GATE: feed() suppresses non-admin button", rec == [])
    rec.clear()
    fnav.feed(E(ecodes.EV_KEY, ecodes.BTN_SOUTH, 1), "/dev/input/event20", "Xbox Wireless Controller")
    check("GATE: feed() emits for admin button", rec == ["Return"])

    print("\n%d test(s) FAILED" % len(failures) if failures else "\nALL TESTS PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run()
