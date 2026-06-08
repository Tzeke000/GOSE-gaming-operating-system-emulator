#!/usr/bin/env python3
"""GOSE host-side CONTROLLER PASSTHROUGH — input-level pad forwarding (py -3.11, Windows).

Why this exists: streaming a physical pad into the QEMU guest over usb-redir
(usbredirect + USBDk) measured 4-7 SECONDS of input lag — a DualSense is a 1 kHz
composite USB device and the redirect layer bufferbloats unfixably at that rate.
So we forward INPUT EVENTS, not the USB device: this daemon reads every real game
controller on the host via SDL's GameController API (pygame), already normalized
to the standard button/axis layout by SDL's gamecontrollerdb (DualSense, Xbox,
8BitDo, generics — all one language), and replays them onto an in-guest uinput
device the GOSE agent creates (`input.pt_open` / `input.pt_event`, phys
"gose-passthrough"). Round trip is milliseconds; the guest sees "a real pad"
with the REAL vendor/product/version ids (SDL GUID match → es_input /
gamecontrollerdb binds work in-guest too).

The pad stays attached to WINDOWS (never usb-redir-claimed); only its events
travel. By design there is no 1 kHz sensor stream — an untouched pad sends
nothing at all.

EVERY pad gets hooked, not just DB-known ones: (1) SDL is pointed at the repo's
vendored gamecontrollerdb.txt (SDL_GAMECONTROLLERCONFIG_FILE, set before pygame
import) so many would-be raw joysticks attach as properly-mapped controllers;
(2) anything STILL not a GameController falls back to the raw joystick API with
the heuristic standard layout most DB rows encode (axes 0/1 left stick, 2/3
right stick, hat 0 dpad, buttons 0..N in SOUTH/EAST/WEST/NORTH/TL/TR/SELECT/
START/MODE/THUMBL/THUMBR order), name-tagged " (unmapped)" for the UI.

Run:    py -3.11 pad_passthrough.py               (daemon; boot-gose-vm.ps1 starts it)
        py -3.11 pad_passthrough.py --once-status  (print detected pads, exit)
Log:    D:\\gose-vm\\pad_passthrough.log
Token:  GOSE_TOKEN env, else parsed from D:\\Wren\\.mcp.json (dev box fallback).
"""
from __future__ import annotations

import json
import os
import re
import socket
import statistics
import sys
import time
import logging

# SDL must see these BEFORE pygame import. Headless: dummy video driver (no window);
# the Windows joystick backend uses its own hidden message window for WM_DEVICECHANGE
# hotplug, and SDL_JOYSTICK_THREAD keeps polling smooth without a real event pump.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_JOYSTICK_THREAD", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# Extra controller mappings: the vendored gamecontrollerdb.txt next to this script
# (SDL reads the env var at controller-subsystem init). Upgrades many would-be raw
# joysticks into properly-mapped GameControllers. setdefault — a caller's own
# SDL_GAMECONTROLLERCONFIG_FILE wins.
_GCDB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gamecontrollerdb.txt")
if os.path.isfile(_GCDB):
    os.environ.setdefault("SDL_GAMECONTROLLERCONFIG_FILE", _GCDB)

import pygame  # noqa: E402
from pygame._sdl2 import controller as sdl_controller  # noqa: E402

GOSE_HOST = os.environ.get("GOSE_HOST", "127.0.0.1")
GOSE_PORT = int(os.environ.get("GOSE_PORT", "8731"))
LOGFILE = os.environ.get("GOSE_PADPT_LOG", r"D:\gose-vm\pad_passthrough.log")
MCP_JSON = r"D:\Wren\.mcp.json"          # dev-box token fallback
POLL_S = 0.002                            # 500 Hz pump (>= the required 250 Hz)
LAT_REPORT_EVERY = 500                    # log p50/p95 every N forwarded batches

# ---- evdev codes (the guest pt device's vocabulary) ----
EV_KEY, EV_ABS = 1, 3
BTN_SOUTH, BTN_EAST, BTN_WEST, BTN_NORTH = 304, 305, 307, 308
BTN_TL, BTN_TR = 310, 311
BTN_SELECT, BTN_START, BTN_MODE = 314, 315, 316
BTN_THUMBL, BTN_THUMBR = 317, 318
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0, 1, 2, 3, 4, 5
ABS_HAT0X, ABS_HAT0Y = 16, 17

# SDL standard button -> evdev EV_KEY code (dpad is handled as ABS_HAT0 below)
BUTTON_MAP = {
    pygame.CONTROLLER_BUTTON_A: BTN_SOUTH,
    pygame.CONTROLLER_BUTTON_B: BTN_EAST,
    pygame.CONTROLLER_BUTTON_X: BTN_WEST,
    pygame.CONTROLLER_BUTTON_Y: BTN_NORTH,
    pygame.CONTROLLER_BUTTON_LEFTSHOULDER: BTN_TL,
    pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: BTN_TR,
    pygame.CONTROLLER_BUTTON_BACK: BTN_SELECT,
    pygame.CONTROLLER_BUTTON_START: BTN_START,
    pygame.CONTROLLER_BUTTON_GUIDE: BTN_MODE,
    pygame.CONTROLLER_BUTTON_LEFTSTICK: BTN_THUMBL,
    pygame.CONTROLLER_BUTTON_RIGHTSTICK: BTN_THUMBR,
}
DPAD_BTNS = {
    pygame.CONTROLLER_BUTTON_DPAD_UP: ("y", -1),
    pygame.CONTROLLER_BUTTON_DPAD_DOWN: ("y", +1),
    pygame.CONTROLLER_BUTTON_DPAD_LEFT: ("x", -1),
    pygame.CONTROLLER_BUTTON_DPAD_RIGHT: ("x", +1),
}
# SDL standard axis -> (evdev ABS code, is_trigger). Sticks pass through raw
# (-32768..32767 both sides); triggers rescale SDL 0..32767 -> evdev 0..255.
AXIS_MAP = {
    pygame.CONTROLLER_AXIS_LEFTX: (ABS_X, False),
    pygame.CONTROLLER_AXIS_LEFTY: (ABS_Y, False),
    pygame.CONTROLLER_AXIS_RIGHTX: (ABS_RX, False),
    pygame.CONTROLLER_AXIS_RIGHTY: (ABS_RY, False),
    pygame.CONTROLLER_AXIS_TRIGGERLEFT: (ABS_Z, True),
    pygame.CONTROLLER_AXIS_TRIGGERRIGHT: (ABS_RZ, True),
}
# Raw-joystick FALLBACK layout (pads SDL has no GameController mapping for):
# the heuristic standard shape most gamecontrollerdb rows encode.
JOYFB_BUTTONS = [BTN_SOUTH, BTN_EAST, BTN_WEST, BTN_NORTH, BTN_TL, BTN_TR,
                 BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR]
JOYFB_AXES = [ABS_X, ABS_Y, ABS_RX, ABS_RY]      # 0/1 left stick, 2/3 right stick

log = logging.getLogger("pad_passthrough")


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOGFILE, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


def resolve_token() -> str:
    tok = os.environ.get("GOSE_TOKEN")
    if tok:
        return tok
    try:
        with open(MCP_JSON, "r", encoding="utf-8") as fh:
            return json.load(fh)["mcpServers"]["gose"]["env"]["GOSE_TOKEN"]
    except Exception:
        return ""


# ---- minimal GOSE agent client (vendored from agent/client/gose_client.py:
# same newline-JSON protocol + the reconnect-once-on-dead-socket behavior) ----
class AgentError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


class AgentClient:
    def __init__(self, host, port, token, timeout=5.0):
        self.host, self.port, self.token, self.timeout = host, port, token, timeout
        self._sock = None
        self._buf = b""
        self._id = 0

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), self.timeout)
        self._sock.settimeout(self.timeout)
        try:
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._buf = b""

    def call(self, op, **args):
        last = None
        for attempt in (1, 2):
            try:
                if self._sock is None:
                    self.connect()
                self._id += 1
                req = {"id": self._id, "op": op, "args": args}
                if self.token:
                    req["token"] = self.token
                self._sock.sendall((json.dumps(req) + "\n").encode())
                while True:
                    while b"\n" not in self._buf:
                        chunk = self._sock.recv(65536)
                        if not chunk:
                            raise OSError("connection closed")
                        self._buf += chunk
                    line, self._buf = self._buf.split(b"\n", 1)
                    msg = json.loads(line)
                    if "event" in msg or msg.get("id") != self._id:
                        continue
                    if not msg.get("ok"):
                        raise AgentError(msg.get("code", "ERR"), msg.get("error", ""))
                    return msg.get("result", {})
            except OSError as e:
                self.close()
                last = e
                if attempt == 1:
                    continue
                raise AgentError("ERR_CONN", str(e))
        raise AgentError("ERR_CONN", str(last))


def parse_sdl_guid(guid_hex: str):
    """bustype/vendor/product/version out of an SDL joystick GUID (LE u16 fields:
    bytes 0-1 bus, 4-5 vendor, 8-9 product, 12-13 version)."""
    g = (guid_hex or "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", g):
        return 3, 0, 0, 0
    le16 = lambda off: int(g[off + 2:off + 4] + g[off:off + 2], 16)  # noqa: E731
    return le16(0), le16(8), le16(16), le16(24)


class Pad:
    """One attached physical controller and its in-guest pt mirror."""

    def __init__(self, ctrl, joystick):
        self.ctrl = ctrl
        self.joy = joystick
        self.instance_id = joystick.get_instance_id()
        self.name = joystick.get_name() or "Controller"
        self.guid = joystick.get_guid()
        bus, vendor, product, version = parse_sdl_guid(self.guid)
        if not vendor or not product:
            # GUID didn't carry usable ids (some XInput paths) — fall back to the
            # Xbox-360 identity, the one GUID guaranteed bindable in-guest.
            bus, vendor, product, version = 3, 0x045E, 0x028E, 0x0110
        self.bustype, self.vendor, self.product, self.version = bus, vendor, product, version
        self.pt_id = None
        self.dpad = {"x": 0, "y": 0}   # current hat state (ternary per axis)

    def open_guest(self, client: AgentClient):
        r = client.call("input.pt_open", name=self.name, vendor=self.vendor,
                        product=self.product, version=self.version, bustype=self.bustype)
        self.pt_id = r["pt_id"]
        return r

    def snapshot_events(self):
        """Current full state (buttons + axes) — sync the guest mirror on attach
        and after a guest-agent restart, so a held trigger/button isn't lost."""
        evs = []
        for btn, code in BUTTON_MAP.items():
            evs.append({"type": EV_KEY, "code": code, "value": 1 if self.ctrl.get_button(btn) else 0})
        self.dpad["x"] = (1 if self.ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_RIGHT) else 0) - \
                         (1 if self.ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_LEFT) else 0)
        self.dpad["y"] = (1 if self.ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_DOWN) else 0) - \
                         (1 if self.ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_UP) else 0)
        evs.append({"type": EV_ABS, "code": ABS_HAT0X, "value": self.dpad["x"]})
        evs.append({"type": EV_ABS, "code": ABS_HAT0Y, "value": self.dpad["y"]})
        for axis, (code, trig) in AXIS_MAP.items():
            evs.append({"type": EV_ABS, "code": code,
                        "value": scale_axis(self.ctrl.get_axis(axis), trig)})
        return evs

    def map_button(self, button: int, down: bool):
        """SDL button event -> list of evdev events (updates dpad state)."""
        if button in BUTTON_MAP:
            return [{"type": EV_KEY, "code": BUTTON_MAP[button], "value": 1 if down else 0}]
        if button in DPAD_BTNS:
            axis, direction = DPAD_BTNS[button]
            if down:
                self.dpad[axis] = direction
            elif self.dpad[axis] == direction:   # release only clears OUR direction
                self.dpad[axis] = 0
            code = ABS_HAT0X if axis == "x" else ABS_HAT0Y
            return [{"type": EV_ABS, "code": code, "value": self.dpad[axis]}]
        return []

    def quit(self):
        try:
            (self.ctrl or self.joy).quit()
        except Exception:
            pass


class JoyPad(Pad):
    """Raw-joystick FALLBACK: a host pad SDL has no GameController mapping for
    (exotic/no-name, not in the DB) still gets hooked and wired up like the
    DualSense — attached via the joystick API with the heuristic standard layout
    (JOYFB_*), name-tagged " (unmapped)" so the UI can surface
    "generic controller — default mapping"."""

    def __init__(self, joystick):
        super().__init__(None, joystick)
        self.name += " (unmapped)"

    def snapshot_events(self):
        evs = []
        for i, code in enumerate(JOYFB_BUTTONS[:self.joy.get_numbuttons()]):
            evs.append({"type": EV_KEY, "code": code,
                        "value": 1 if self.joy.get_button(i) else 0})
        if self.joy.get_numhats() > 0:
            hx, hy = self.joy.get_hat(0)
            self.dpad["x"], self.dpad["y"] = hx, -hy   # pygame hat y is + up; HAT0Y is - up
        evs.append({"type": EV_ABS, "code": ABS_HAT0X, "value": self.dpad["x"]})
        evs.append({"type": EV_ABS, "code": ABS_HAT0Y, "value": self.dpad["y"]})
        for i, code in enumerate(JOYFB_AXES[:self.joy.get_numaxes()]):
            evs.append({"type": EV_ABS, "code": code,
                        "value": scale_jaxis(self.joy.get_axis(i))})
        return evs

    def map_jbutton(self, button: int, down: bool):
        if button < len(JOYFB_BUTTONS):
            return [{"type": EV_KEY, "code": JOYFB_BUTTONS[button], "value": 1 if down else 0}]
        return []

    def map_jaxis(self, axis: int, value: float):
        if axis < len(JOYFB_AXES):
            return [{"type": EV_ABS, "code": JOYFB_AXES[axis], "value": scale_jaxis(value)}]
        return []

    def map_jhat(self, hat: int, value):
        if hat != 0:
            return []
        hx, hy = value
        self.dpad["x"], self.dpad["y"] = hx, -hy
        return [{"type": EV_ABS, "code": ABS_HAT0X, "value": self.dpad["x"]},
                {"type": EV_ABS, "code": ABS_HAT0Y, "value": self.dpad["y"]}]


def scale_axis(value: int, is_trigger: bool) -> int:
    if is_trigger:                       # SDL 0..32767 -> evdev 0..255
        return max(0, min(255, value * 255 // 32767))
    return max(-32768, min(32767, value))


def scale_jaxis(value: float) -> int:
    """Raw joystick axis (pygame float -1..1) -> evdev stick range."""
    return max(-32768, min(32767, int(value * 32767)))


class Forwarder:
    def __init__(self):
        self.client = AgentClient(GOSE_HOST, GOSE_PORT, resolve_token())
        self.pads = {}                   # instance_id -> Pad
        self.lat = []                    # round-trip seconds per forwarded batch
        self.batches = 0

    # ---- agent availability ----
    def wait_for_agent(self):
        notice = 0
        while True:
            try:
                self.client.call("ping")
                log.info("GOSE agent reachable at %s:%d", GOSE_HOST, GOSE_PORT)
                return
            except Exception as e:
                if notice % 30 == 0:
                    log.info("waiting for GOSE agent (%s) ...", e)
                notice += 1
                time.sleep(2.0)

    # ---- pad lifecycle ----
    def attach(self, device_index: int):
        """Mapped GameController path (CONTROLLERDEVICEADDED)."""
        try:
            if not sdl_controller.is_controller(device_index):
                return                   # raw joystick — attach_joystick handles it
            ctrl = sdl_controller.Controller(device_index)
            pad = Pad(ctrl, ctrl.as_joystick())
        except Exception as e:
            log.warning("could not open device %d: %s", device_index, e)
            return
        self._register(pad)

    def attach_joystick(self, device_index: int):
        """Fallback path (JOYDEVICEADDED, not a GameController): hook it anyway
        with the heuristic default layout instead of ignoring it."""
        try:
            if sdl_controller.is_controller(device_index):
                return                   # mapped — attach() handles it
            pad = JoyPad(pygame.joystick.Joystick(device_index))
        except Exception as e:
            log.warning("could not open joystick %d: %s", device_index, e)
            return
        log.info("FALLBACK device %d '%s': no GameController mapping — raw joystick "
                 "with default layout (%d buttons, %d axes, %d hats)",
                 device_index, pad.name, pad.joy.get_numbuttons(),
                 pad.joy.get_numaxes(), pad.joy.get_numhats())
        self._register(pad)

    def _register(self, pad: Pad):
        if pad.instance_id in self.pads:
            pad.quit()                   # duplicate hotplug notification (SDL refcounts opens)
            return
        try:
            r = pad.open_guest(self.client)
            self.client.call("input.pt_event", pt_id=pad.pt_id, events=pad.snapshot_events())
        except Exception as e:
            log.error("guest pt_open failed for '%s': %s", pad.name, e)
            pad.quit()
            return
        self.pads[pad.instance_id] = pad
        log.info("ATTACH '%s' guid=%s vid=%04x pid=%04x ver=%04x bus=%d -> pt_id=%s (%s)",
                 pad.name, pad.guid, pad.vendor, pad.product, pad.version,
                 pad.bustype, pad.pt_id, r.get("backend"))

    def detach(self, instance_id: int):
        pad = self.pads.pop(instance_id, None)
        if not pad:
            return
        try:
            self.client.call("input.pt_close", pt_id=pad.pt_id)
        except Exception as e:
            log.warning("pt_close failed for '%s': %s", pad.name, e)
        pad.quit()
        self.report_latency(final=True)
        log.info("DETACH '%s' (pt_id=%s)", pad.name, pad.pt_id)

    def reopen(self, pad: Pad):
        """The guest agent restarted (pt ids are gone) — recreate the mirror."""
        try:
            pad.open_guest(self.client)
            self.client.call("input.pt_event", pt_id=pad.pt_id, events=pad.snapshot_events())
            log.info("REOPEN '%s' -> pt_id=%s (agent restart recovered)", pad.name, pad.pt_id)
            return True
        except Exception as e:
            log.warning("reopen failed for '%s': %s", pad.name, e)
            return False

    # ---- event forwarding ----
    def send(self, pad: Pad, events):
        if not events:
            return
        t0 = time.perf_counter()
        try:
            self.client.call("input.pt_event", pt_id=pad.pt_id, events=events)
        except AgentError as e:
            # stale pt_id (agent restarted) or dropped conn -> one recovery attempt
            if self.reopen(pad):
                try:
                    self.client.call("input.pt_event", pt_id=pad.pt_id, events=events)
                except Exception as e2:
                    log.warning("forward failed after reopen: %s", e2)
                    return
            else:
                log.warning("forward failed: %s", e)
                return
        self.lat.append(time.perf_counter() - t0)
        self.batches += 1
        if self.batches % LAT_REPORT_EVERY == 0:
            self.report_latency()

    def report_latency(self, final=False):
        if len(self.lat) < 2:
            return
        ms = sorted(v * 1000 for v in self.lat)
        p50 = statistics.median(ms)
        p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
        log.info("latency host->guest-uinput round trip: n=%d p50=%.2fms p95=%.2fms max=%.2fms%s",
                 len(ms), p50, p95, ms[-1], " (final)" if final else "")
        if len(self.lat) > 5000:
            self.lat = self.lat[-1000:]

    def pump(self):
        """Drain SDL events; coalesce into one pt_event batch per pad per pump."""
        batches = {}
        for ev in pygame.event.get():
            if ev.type == pygame.CONTROLLERDEVICEADDED:
                self.attach(ev.device_index)
            elif ev.type == pygame.JOYDEVICEADDED:
                self.attach_joystick(ev.device_index)     # no-op for mapped controllers
            elif ev.type in (pygame.CONTROLLERDEVICEREMOVED, pygame.JOYDEVICEREMOVED):
                self.detach(ev.instance_id)               # pop-idempotent (both fire for controllers)
            elif ev.type in (pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLERBUTTONUP):
                pad = self.pads.get(ev.instance_id)
                if pad:
                    batches.setdefault(ev.instance_id, []).extend(
                        pad.map_button(ev.button, ev.type == pygame.CONTROLLERBUTTONDOWN))
            elif ev.type == pygame.CONTROLLERAXISMOTION:
                pad = self.pads.get(ev.instance_id)
                if pad and ev.axis in AXIS_MAP:
                    code, trig = AXIS_MAP[ev.axis]
                    batches.setdefault(ev.instance_id, []).append(
                        {"type": EV_ABS, "code": code, "value": scale_axis(ev.value, trig)})
            # raw-joystick fallback events: JoyPad ONLY — a mapped controller also
            # emits JOY* for the same motion, and forwarding both would double-fire
            elif ev.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                pad = self.pads.get(ev.instance_id)
                if isinstance(pad, JoyPad):
                    batches.setdefault(ev.instance_id, []).extend(
                        pad.map_jbutton(ev.button, ev.type == pygame.JOYBUTTONDOWN))
            elif ev.type == pygame.JOYAXISMOTION:
                pad = self.pads.get(ev.instance_id)
                if isinstance(pad, JoyPad):
                    batches.setdefault(ev.instance_id, []).extend(
                        pad.map_jaxis(ev.axis, ev.value))
            elif ev.type == pygame.JOYHATMOTION:
                pad = self.pads.get(ev.instance_id)
                if isinstance(pad, JoyPad):
                    batches.setdefault(ev.instance_id, []).extend(
                        pad.map_jhat(ev.hat, ev.value))
        for iid, events in batches.items():
            pad = self.pads.get(iid)   # a pad can be popped by a REMOVED event in THIS same
            if pad:                    # pump (unplug delivers final axis events + REMOVED together),
                self.send(pad, events) # so guard — an unplug must never KeyError out of run()

    def cleanup_orphans(self):
        """pt devices only ever come from this daemon — any open at OUR startup are
        leftovers of a previous run (host restart without agent restart). Close them
        so they don't leak toward the MAX-4 cap."""
        try:
            for d in self.client.call("input.pt_list").get("open", []):
                self.client.call("input.pt_close", pt_id=d["pt_id"])
                log.info("closed orphan pt device pt_id=%s ('%s')", d["pt_id"], d.get("name"))
        except Exception as e:
            log.warning("orphan cleanup skipped: %s", e)

    def run(self):
        log.info("pad passthrough up: SDL %s, video=%s, polling %.0f Hz, extra mappings=%s",
                 pygame.version.SDL, os.environ.get("SDL_VIDEODRIVER"), 1 / POLL_S,
                 os.environ.get("SDL_GAMECONTROLLERCONFIG_FILE") or "none (built-ins only)")
        self.wait_for_agent()
        self.cleanup_orphans()
        while True:
            try:
                self.pump()
            except Exception as e:
                # never let one bad pump (e.g. a pad vanishing mid-batch) kill the daemon;
                # log and keep forwarding. The watchdog is the backstop if we somehow still die.
                log.warning("pump error (continuing): %s", e)
            time.sleep(POLL_S)


def init_sdl():
    pygame.init()
    sdl_controller.init()
    try:
        sdl_controller.set_eventstate(True)   # make sure CONTROLLER* events flow
    except Exception:
        pass


def once_status():
    init_sdl()
    time.sleep(0.5)                       # let the joystick thread enumerate
    pygame.event.get()                    # deliver CONTROLLERDEVICEADDED
    n = pygame.joystick.get_count()
    print("SDL %s — %d joystick device(s)" % (pygame.version.SDL, n))
    for i in range(n):
        j = pygame.joystick.Joystick(i)
        guid = j.get_guid()
        bus, vid, pid, ver = parse_sdl_guid(guid)
        is_gc = sdl_controller.is_controller(i)
        print("  [%d] %s  guid=%s  vid=%04x pid=%04x ver=%04x bus=%d  gamecontroller=%s"
              % (i, j.get_name(), guid, vid, pid, ver, bus, is_gc))
    return 0


def main():
    setup_logging()
    if "--once-status" in sys.argv:
        return once_status()
    init_sdl()
    Forwarder().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
