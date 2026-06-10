"""Input capability: inject gamepad + keyboard events.

Real backend creates a virtual gamepad via Linux `uinput` (through python-evdev),
so emulators see the AI as just another controller. Mock backend records events
(used in CI / cloud container where /dev/uinput isn't available).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from typing import Dict, List

from ..protocol import AgentError, ERR_ARGS, ERR_BACKEND

log = logging.getLogger("gose.agent.input")

# Standard pad vocabulary exposed over the protocol.
BUTTONS = [
    "a", "b", "x", "y", "up", "down", "left", "right",
    "l1", "r1", "l2", "r2", "l3", "r3", "start", "select", "guide",
]
AXES = ["lx", "ly", "rx", "ry", "lt", "rt"]


class BaseInput:
    backend = "base"

    def info(self) -> Dict:
        return {"backend": self.backend, "buttons": BUTTONS, "axes": AXES}

    def button(self, name: str, action: str, duration_ms: int = 80) -> Dict:
        if name not in BUTTONS:
            raise AgentError(ERR_ARGS, f"unknown button '{name}'")
        if action not in ("press", "release", "tap"):
            raise AgentError(ERR_ARGS, f"unknown action '{action}'")
        return self._button(name, action, duration_ms)

    def combo(self, buttons: List[str], duration_ms: int = 80) -> Dict:
        for b in buttons:
            if b not in BUTTONS:
                raise AgentError(ERR_ARGS, f"unknown button '{b}'")
        return self._combo(buttons, duration_ms)

    def axis(self, name: str, value: float) -> Dict:
        if name not in AXES:
            raise AgentError(ERR_ARGS, f"unknown axis '{name}'")
        value = max(-1.0, min(1.0, float(value)))
        return self._axis(name, value)

    def type_text(self, text: str) -> Dict:
        return self._type(str(text))

    # ---- backend hooks ----
    def _button(self, name, action, duration_ms): raise NotImplementedError
    def _combo(self, buttons, duration_ms): raise NotImplementedError
    def _axis(self, name, value): raise NotImplementedError
    def _type(self, text): raise NotImplementedError


class MockInput(BaseInput):
    backend = "mock"

    def __init__(self, name: str = "AI virtual controller"):
        # name accepted (and ignored) so MockInput is a drop-in seat factory, same
        # signature as EvdevInput — SeatManager calls factory(seat).
        self.name = name
        self.events: List[Dict] = []  # inspectable in tests

    def _record(self, **kw):
        kw["ts"] = time.time()
        self.events.append(kw)
        return {"done": True, "mock": True}

    def _button(self, name, action, duration_ms):
        return self._record(kind="button", name=name, action=action, duration_ms=duration_ms)

    def _combo(self, buttons, duration_ms):
        return self._record(kind="combo", buttons=list(buttons), duration_ms=duration_ms)

    def _axis(self, name, value):
        return self._record(kind="axis", name=name, value=value)

    def _type(self, text):
        return self._record(kind="type", text=text)


class EvdevInput(BaseInput):
    """Real uinput-backed gamepad/keyboard. Imported lazily; only on device."""
    backend = "evdev"

    def __init__(self, name: str = "AI virtual controller"):
        import evdev
        from evdev import ecodes as e
        self._e = e
        # Map our names to evdev key/abs codes for a standard pad.
        self._btn = {
            "a": e.BTN_SOUTH, "b": e.BTN_EAST, "x": e.BTN_WEST, "y": e.BTN_NORTH,
            "l1": e.BTN_TL, "r1": e.BTN_TR, "l3": e.BTN_THUMBL, "r3": e.BTN_THUMBR,
            "start": e.BTN_START, "select": e.BTN_SELECT, "guide": e.BTN_MODE,
        }
        # D-pad + triggers go through ABS axes (hat / Z / RZ).
        # EV_KEY capability set: declare PT_KEYS (the full 17-key passthrough set)
        # rather than just the 11 buttons this pad actively fires. This aligns the
        # udev/SDL ascending-keycode button indices with the PT pad so the shared
        # Xbox-360 GUID has one consistent es_input entry for both pad types.
        # The _btn map (11 keys) is unchanged — we only DECLARE the extras, never
        # fire them. BTN_START lands at index 9 in both pad types this way.
        cap = {
            e.EV_KEY: list(PT_KEYS),
            e.EV_ABS: [
                (e.ABS_X, evdev.AbsInfo(0, -32768, 32767, 0, 0, 0)),
                (e.ABS_Y, evdev.AbsInfo(0, -32768, 32767, 0, 0, 0)),
                (e.ABS_RX, evdev.AbsInfo(0, -32768, 32767, 0, 0, 0)),
                (e.ABS_RY, evdev.AbsInfo(0, -32768, 32767, 0, 0, 0)),
                (e.ABS_Z, evdev.AbsInfo(0, 0, 255, 0, 0, 0)),
                (e.ABS_RZ, evdev.AbsInfo(0, 0, 255, 0, 0, 0)),
                (e.ABS_HAT0X, evdev.AbsInfo(0, -1, 1, 0, 0, 0)),
                (e.ABS_HAT0Y, evdev.AbsInfo(0, -1, 1, 0, 0, 0)),
            ],
        }
        # IDENTITY stays an Xbox 360 pad (vendor 045e / product 028e / version 0x0110) so its
        # SDL GUID (030000005e0400008e02000010010000) matches es_input.cfg + gamecontrollerdb → the
        # OS gives it real button bindings and games actually accept its input. Without a known
        # identity the pad is only DETECTED, never BOUND (the bug that made "AI plays games"
        # silently not work). The device NAME, however, is ours: "AI virtual controller [N]" so it
        # reads as what it is (an AI seat) in controller lists / the AI Hub. The name is a separate
        # uinput string and does NOT enter the GUID — keep vendor/product/version IDENTICAL.
        self._ui = evdev.UInput(cap, name=name,
                                vendor=0x045e, product=0x028e, version=0x0110)
        self._hat = {  # dpad -> (axis, value)
            "up": (e.ABS_HAT0Y, -1), "down": (e.ABS_HAT0Y, 1),
            "left": (e.ABS_HAT0X, -1), "right": (e.ABS_HAT0X, 1),
        }
        self._trig = {"l2": e.ABS_Z, "r2": e.ABS_RZ}

    def _press_raw(self, name, down):
        e = self._e
        if name in self._btn:
            self._ui.write(e.EV_KEY, self._btn[name], 1 if down else 0)
        elif name in self._hat:
            ax, val = self._hat[name]
            self._ui.write(e.EV_ABS, ax, val if down else 0)
        elif name in self._trig:
            self._ui.write(e.EV_ABS, self._trig[name], 255 if down else 0)
        self._ui.syn()

    def _button(self, name, action, duration_ms):
        if action == "press":
            self._press_raw(name, True)
        elif action == "release":
            self._press_raw(name, False)
        else:  # tap
            self._press_raw(name, True)
            time.sleep(duration_ms / 1000.0)
            self._press_raw(name, False)
        return {"done": True}

    def _combo(self, buttons, duration_ms):
        for b in buttons:
            self._press_raw(b, True)
        time.sleep(duration_ms / 1000.0)
        for b in reversed(buttons):
            self._press_raw(b, False)
        return {"done": True}

    def _axis(self, name, value):
        e = self._e
        amap = {"lx": e.ABS_X, "ly": e.ABS_Y, "rx": e.ABS_RX, "ry": e.ABS_RY}
        if name in amap:
            self._ui.write(e.EV_ABS, amap[name], int(value * 32767))
        elif name == "lt":
            self._ui.write(e.EV_ABS, e.ABS_Z, int(abs(value) * 255))
        elif name == "rt":
            self._ui.write(e.EV_ABS, e.ABS_RZ, int(abs(value) * 255))
        self._ui.syn()
        return {"done": True}

    def _type(self, text):
        # Text entry maps to the on-screen keyboard in practice; a raw uinput
        # keyboard is a future addition. For now report unsupported clearly.
        raise AgentError(ERR_BACKEND, "type_text not yet wired on evdev backend")


# ---------------------------------------------------------------------------
# Host-pad PASSTHROUGH (input-level controller forwarding)
#
# Why: streaming a physical pad into the VM over usb-redir (USBDk) measured
# 4-7 s of input lag — a DualSense is a 1 kHz composite USB device and the
# redirect layer bufferbloats unfixably. So instead of forwarding the USB
# *device*, the host forwards *input events*: a host daemon (pad_passthrough.py)
# reads the real pad via SDL and replays its state onto an in-guest uinput
# device created here. Millisecond round trips; the guest still sees "a real
# controller".
#
# Identity: pt_open creates the uinput device with the REAL pad's
# vendor/product/version/bustype, so the SDL GUID consumers compute from the
# kernel ids matches the physical pad → gamecontrollerdb / es_input binds work.
# The device name is the real pad's name too. What marks it as ours is
# phys="gose-passthrough" — the controller registry (gose_vm_server) keys off
# that to classify it source="passthrough" (NOT "virtual": it is the human's
# pad, first-class player + admin-eligible).
# ---------------------------------------------------------------------------
PT_PHYS = "gose-passthrough"
_PT_EV_KEY = 1   # evdev EV_KEY
_PT_EV_ABS = 3   # evdev EV_ABS

# The full EV_KEY capability set every passthrough uinput device exposes —
# EvdevPassthroughDevice builds its caps from THIS list, and the es_input
# button ids are computed from THIS list (udev_button_indices below), so the
# two can never drift apart. Numeric kernel keycodes (stable ABI), so the
# module needs no evdev import: BTN_SOUTH..BTN_THUMBR + BTN_DPAD_*.
PT_KEYS = [
    0x130, 0x131, 0x133, 0x134,  # BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST
    0x136, 0x137, 0x138, 0x139,  # BTN_TL, BTN_TR, BTN_TL2, BTN_TR2
    0x13a, 0x13b, 0x13c,         # BTN_SELECT, BTN_START, BTN_MODE
    0x13d, 0x13e,                # BTN_THUMBL, BTN_THUMBR
    0x220, 0x221, 0x222, 0x223,  # BTN_DPAD_UP/DOWN/LEFT/RIGHT
]


class MockPassthroughDevice:
    """CI/no-uinput stand-in: records injected events (inspectable in tests)."""
    backend = "mock"

    def __init__(self, name, vendor, product, version, bustype):
        self.name, self.vendor, self.product = name, vendor, product
        self.version, self.bustype = version, bustype
        self.events: List[tuple] = []

    def inject(self, events):
        self.events.extend(events)

    def close(self):
        pass


class EvdevPassthroughDevice:
    """Real uinput device mirroring a physical host pad (full standard caps)."""
    backend = "evdev"

    def __init__(self, name, vendor, product, version, bustype):
        import evdev
        from evdev import ecodes as e
        self.name, self.vendor, self.product = name, vendor, product
        self.version, self.bustype = version, bustype
        cap = {
            e.EV_KEY: list(PT_KEYS),
            e.EV_ABS: [
                (e.ABS_X, evdev.AbsInfo(0, -32768, 32767, 16, 128, 0)),
                (e.ABS_Y, evdev.AbsInfo(0, -32768, 32767, 16, 128, 0)),
                (e.ABS_RX, evdev.AbsInfo(0, -32768, 32767, 16, 128, 0)),
                (e.ABS_RY, evdev.AbsInfo(0, -32768, 32767, 16, 128, 0)),
                (e.ABS_Z, evdev.AbsInfo(0, 0, 255, 0, 0, 0)),
                (e.ABS_RZ, evdev.AbsInfo(0, 0, 255, 0, 0, 0)),
                (e.ABS_HAT0X, evdev.AbsInfo(0, -1, 1, 0, 0, 0)),
                (e.ABS_HAT0Y, evdev.AbsInfo(0, -1, 1, 0, 0, 0)),
            ],
        }
        self._ui = evdev.UInput(cap, name=name, vendor=vendor, product=product,
                                version=version, bustype=bustype, phys=PT_PHYS)

    def inject(self, events):
        for (etype, code, value) in events:
            self._ui.write(etype, code, value)
        self._ui.syn()

    def close(self):
        try:
            self._ui.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# es_input.cfg auto-registration (launcher pairing for passthrough pads)
#
# Why: the game launcher's configgen refuses to launch when a player pad's SDL
# GUID has no <inputConfig> entry — exit 250 "Could not find controller data
# for GUID", hit on the first real DualSense launch (2026-06-07). Stock
# Batocera only knows stock GUIDs; a passthrough pad carries the REAL pad's
# vid/pid, so ANY brand the owner plugs in needs its entry added. pt_open does
# it automatically, computing the button ids from the device's ACTUAL key set
# (see udev_button_indices below).
#
# Safety (this file gates ALL pads' launches — corruption is the catastrophic
# failure): parse + append via ElementTree preserving existing entries AND
# comments; atomic tmp+rename write; absent file → create with wrapper;
# malformed file → back it up loudly and recreate (never silently drop it);
# a module lock serializes concurrent pt_opens (dispatch runs in a thread
# pool); and a registration failure never blocks the pad itself.
# ---------------------------------------------------------------------------
ES_INPUT_CFG = "/userdata/system/configs/emulationstation/es_input.cfg"


def udev_button_indices(keys: List[int]) -> Dict[int, int]:
    """Map each EV_KEY code to the button index its consumers will use.

    The es_input `id` for a button is NOT free-form: configgen copies it
    verbatim into RetroArch's `input_playerN_*_btn`, and RetroArch runs
    `input_joypad_driver = udev` (libretroConfig.py), whose driver assigns
    button indices by SCANNING THE DEVICE'S ACTUAL EV_KEY SET IN ASCENDING
    KEYCODE ORDER — first the cursor block KEY_UP..KEY_DOWN, then
    BTN_MISC..KEY_MAX (RetroArch udev_joypad.c). SDL2 (ES menus + sdl2-based
    emulators) enumerates the same ascending order for gamepad-range codes.

    So the index of e.g. BTN_START depends on WHICH OTHER KEYS the device
    exposes. A real Xbox 360 pad (11 keys, no BTN_TL2/TR2/DPAD_*) puts
    BTN_START at index 7 — the stock entry's id. Our passthrough mirrors
    expose 17 keys, so BTN_TL2(0x138)/BTN_TR2(0x139) land at 6/7 and shift
    select/start/hotkey/l3/r3 to 8/9/10/11/12. Copying the Xbox ids was the
    2026-06-07 shifted-labels bug: in-game "start" landed on R2-click
    (empirically proven — BTN_TR2 started pong, BTN_START did nothing).
    """
    keys = set(keys)
    order = [k for k in range(103, 109) if k in keys]        # KEY_UP..KEY_DOWN
    order += [k for k in range(0x100, 0x300) if k in keys]   # BTN_MISC..KEY_MAX
    return {code: i for i, code in enumerate(order)}


# ES bind name -> EV_KEY code (Batocera's semantic layout, same naming its
# stock entries use: "a"=BTN_EAST, "b"=BTN_SOUTH, pageup/pagedown=L1/R1...).
_ES_BUTTON_CODES = [
    ("a", 0x131), ("b", 0x130), ("x", 0x134), ("y", 0x133),
    ("pageup", 0x136), ("pagedown", 0x137),
    ("select", 0x13a), ("start", 0x13b), ("hotkey", 0x13c),
    ("l3", 0x13d), ("r3", 0x13e),
]

# Axis/hat binds are NOT computed per-device: every pt device exposes the same
# EV_ABS set (X/Y/RX/RY/Z/RZ/HAT0X/HAT0Y), which the udev driver indexes 0-5
# in ABS-code order with HAT0 as hat 0 — identical to the Xbox-360 layout
# these ids describe. Dpad rides ABS_HAT0 (the host daemon never sends
# BTN_DPAD_* key events), triggers ride ABS_Z/RZ.
_ES_AXIS_HAT_BINDS = [
    # (name, type, id, value, code)
    ("down", "hat", "0", "4", "16"),
    ("joystick1left", "axis", "0", "-1", "0"),
    ("joystick1up", "axis", "1", "-1", "1"),
    ("joystick2left", "axis", "3", "-1", "3"),
    ("joystick2up", "axis", "4", "-1", "4"),
    ("l2", "axis", "2", "1", "2"),
    ("left", "hat", "0", "8", "16"),
    ("r2", "axis", "5", "1", "5"),
    ("right", "hat", "0", "2", "16"),
    ("up", "hat", "0", "1", "16"),
]


def es_binds(keys: List[int]) -> List[tuple]:
    """The (name, type, id, value, code) rows for a pad exposing `keys`,
    with button ids computed per the udev/SDL ascending-keycode model.
    Buttons whose keycode the device doesn't expose are omitted."""
    idx = udev_button_indices(keys)
    rows = [(n, "button", str(idx[c]), "1", str(c))
            for n, c in _ES_BUTTON_CODES if c in idx]
    return sorted(rows + _ES_AXIS_HAT_BINDS)


_es_lock = threading.Lock()


def sdl_guid(bustype: int, vendor: int, product: int, version: int) -> str:
    """SDL2 joystick GUID from the kernel ids — the exact formula the launcher's
    configgen uses (and gose_vm_server._sdl_guid): LE u16 fields, zero crc/driver."""
    def le(v):
        return "%02x%02x" % (v & 0xFF, (v >> 8) & 0xFF)
    return (le(bustype) + "0000" + le(vendor) + "0000"
            + le(product) + "0000" + le(version) + "0000")


def _es_entry(name: str, guid: str, binds: List[tuple]) -> ET.Element:
    e = ET.Element("inputConfig",
                   {"type": "joystick", "deviceName": name, "deviceGUID": guid})
    for n, t, i, v, c in binds:
        ET.SubElement(e, "input",
                      {"name": n, "type": t, "id": i, "value": v, "code": c})
    return e


def _es_write_atomic(path: str, root: ET.Element) -> None:
    """Serialize + atomically replace: a crash mid-write can never leave the
    launcher a truncated cfg (which would break EVERY pad's launches)."""
    tree = ET.ElementTree(root)
    ET.indent(tree, space="\t")
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".es_input.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            tree.write(fh, encoding="utf-8", xml_declaration=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_es_input_entry(name: str, vendor: int, product: int, version: int,
                          bustype: int, path: str = ES_INPUT_CFG,
                          keys: List[int] = None) -> Dict:
    """Idempotently ensure es_input.cfg has a CORRECT <inputConfig> for this
    device's GUID, with button ids computed from the device's key set `keys`
    (default: the canonical passthrough set PT_KEYS) per the udev model.

    Other entries (incl. hand-written ones) and comments are preserved. An
    existing entry for the same GUID whose binds disagree with the computed
    ones is corrected IN PLACE (keeping its deviceName) — this self-heals
    entries written before the shifted-labels fix. Only entries for the GUID
    of a pt device this agent opens are ever touched."""
    keys = PT_KEYS if keys is None else keys
    guid = sdl_guid(bustype, vendor, product, version)
    want = es_binds(keys)
    with _es_lock:
        root = None
        if os.path.exists(path):
            try:
                # insert_comments keeps the file's comments through the rewrite.
                parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
                root = ET.parse(path, parser=parser).getroot()
                if root.tag != "inputList":
                    raise ValueError("root tag %r, expected 'inputList'" % root.tag)
            except Exception as exc:
                bak = path + ".bad-" + time.strftime("%Y%m%d-%H%M%S")
                try:
                    os.replace(path, bak)
                except OSError:
                    bak = "(could not move aside)"
                log.error("es_input.cfg unreadable (%s) — backed up to %s, "
                          "recreating with the passthrough entry only; rerun the "
                          "launcher's controller config for other pads", exc, bak)
                root = None
        if root is None:
            root = ET.Element("inputList")
        for ic in root.iter("inputConfig"):
            if ic.get("deviceGUID") != guid:
                continue
            have = sorted((i.get("name"), i.get("type"), i.get("id"),
                           i.get("value"), i.get("code"))
                          for i in ic.findall("input"))
            if have == want:
                return {"es_input": "present", "guid": guid}
            # Stale/mislabelled entry for OUR pt device (pre-fix shape, e.g.
            # Xbox-copied ids) → rewrite its binds in place, keep its name.
            for i in list(ic.findall("input")):
                ic.remove(i)
            for n, t, i_, v, c in want:
                ET.SubElement(ic, "input",
                              {"name": n, "type": t, "id": i_, "value": v, "code": c})
            _es_write_atomic(path, root)
            log.info("es_input.cfg: corrected stale binds for '%s' (GUID %s)",
                     ic.get("deviceName"), guid)
            return {"es_input": "corrected", "guid": guid}
        root.append(_es_entry(name, guid, want))
        _es_write_atomic(path, root)
        log.info("es_input.cfg: registered '%s' (GUID %s) for the launcher", name, guid)
        return {"es_input": "added", "guid": guid}


class PassthroughManager:
    """pt_open/pt_event/pt_close: one uinput mirror per physical host pad."""
    MAX = 4

    def __init__(self, force_mock: bool = False):
        self._force_mock = force_mock
        self._devices: Dict[int, object] = {}
        self._next_id = 1

    def _make(self, name, vendor, product, version, bustype):
        if not self._force_mock:
            try:
                if os.access("/dev/uinput", os.W_OK):
                    import evdev  # noqa: F401
                    return EvdevPassthroughDevice(name, vendor, product, version, bustype)
            except Exception:
                pass
        return MockPassthroughDevice(name, vendor, product, version, bustype)

    @staticmethod
    def _id16(args, key, default=None):
        v = args.get(key, default)
        if v is None:
            raise AgentError(ERR_ARGS, f"missing required arg '{key}'")
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise AgentError(ERR_ARGS, f"'{key}' must be an integer")
        if not 0 <= v <= 0xFFFF:
            raise AgentError(ERR_ARGS, f"'{key}' must be 0..65535")
        return v

    def open(self, args: Dict) -> Dict:
        if len(self._devices) >= self.MAX:
            raise AgentError(ERR_ARGS, f"max {self.MAX} passthrough pads already open")
        name = str(args.get("name") or "Passthrough controller")[:80]
        vendor = self._id16(args, "vendor")
        product = self._id16(args, "product")
        version = self._id16(args, "version", 0)
        bustype = self._id16(args, "bustype", 3)           # default BUS_USB
        dev = self._make(name, vendor, product, version, bustype)
        pt_id = self._next_id
        self._next_id += 1
        self._devices[pt_id] = dev
        # Auto-register the pad with the launcher (see the es_input block above):
        # without an <inputConfig> for its GUID, game launches exit 250. Only on
        # the real backend (mock = CI/dev box, no /userdata) unless a test points
        # GOSE_ES_INPUT_CFG somewhere. A failure here must never block the pad —
        # already-registered pads keep working; we log and report instead.
        es = "skipped"
        try:
            if dev.backend == "evdev" or "GOSE_ES_INPUT_CFG" in os.environ:
                cfg = os.environ.get("GOSE_ES_INPUT_CFG", ES_INPUT_CFG)
                es = ensure_es_input_entry(name, vendor, product, version,
                                           bustype, cfg)["es_input"]
        except Exception as exc:
            log.error("es_input auto-register failed for '%s': %s", name, exc)
            es = "error: %s" % exc
        return {"pt_id": pt_id, "name": name, "phys": PT_PHYS,
                "backend": dev.backend, "es_input": es,
                "open": sorted(self._devices)}

    def _dev(self, pt_id):
        try:
            pt_id = int(pt_id)
        except (TypeError, ValueError):
            raise AgentError(ERR_ARGS, "pt_id must be an integer")
        dev = self._devices.get(pt_id)
        if dev is None:
            raise AgentError(ERR_ARGS,
                             f"pt_id {pt_id} not open (open: {sorted(self._devices)})")
        return dev

    def event(self, pt_id, events) -> Dict:
        dev = self._dev(pt_id)
        if not isinstance(events, list) or not events:
            raise AgentError(ERR_ARGS, "events must be a non-empty list")
        batch = []
        for ev in events:
            try:
                etype, code, value = int(ev["type"]), int(ev["code"]), int(ev["value"])
            except (TypeError, KeyError, ValueError):
                raise AgentError(ERR_ARGS,
                                 "each event needs integer 'type', 'code', 'value'")
            if etype not in (_PT_EV_KEY, _PT_EV_ABS):
                raise AgentError(ERR_ARGS, "event type must be EV_KEY(1) or EV_ABS(3)")
            batch.append((etype, code, value))
        dev.inject(batch)
        return {"done": True, "n": len(batch)}

    def close(self, pt_id) -> Dict:
        dev = self._dev(pt_id)
        self._devices.pop(int(pt_id), None)
        dev.close()
        return {"closed": True, "open": sorted(self._devices)}

    def list(self) -> Dict:
        return {"open": [{"pt_id": k, "name": d.name, "backend": d.backend,
                          "vendor": d.vendor, "product": d.product}
                         for k, d in sorted(self._devices.items())],
                "max": self.MAX}


class SeatManager:
    """Multiplayer seats: one virtual controller per seat (docs/16 + multiplayer plan).

    Seat 1 is created at startup (back-compat: it IS the original single pad).
    More seats open on demand up to MAX_SEATS; each evdev seat is its own uinput
    pad, so the OS/emulator sees one controller per player. Seat order == pad
    creation order == the player order `_virtual_pad_args` passes at game launch.
    """
    MAX_SEATS = 4

    def __init__(self, factory, force_mock: bool = False):
        self._factory = factory          # (seat:int) -> BaseInput
        self._seats: Dict[int, BaseInput] = {1: factory(1)}
        # Host-pad passthrough devices live beside the AI seats (input.pt_* ops).
        # They are NOT seats: a passthrough pad mirrors a human's physical pad.
        self.pt = PassthroughManager(force_mock=force_mock)

    @property
    def backend(self) -> str:
        return self._seats[1].backend

    @property
    def events(self):
        """Mock-backend introspection (tests/CI): seat 1's recorded events."""
        return self._seats[1].events

    def info(self) -> Dict:
        d = self._seats[1].info()
        d["seats"] = sorted(self._seats.keys())
        d["max_seats"] = self.MAX_SEATS
        return d

    def seats(self) -> Dict:
        return {"seats": sorted(self._seats.keys()), "max_seats": self.MAX_SEATS}

    def seat_open(self, seat: int) -> Dict:
        seat = int(seat)
        if not 1 <= seat <= self.MAX_SEATS:
            raise AgentError(ERR_ARGS, f"seat must be 1..{self.MAX_SEATS}")
        if seat not in self._seats:
            self._seats[seat] = self._factory(seat)
        return self.seats()

    def seat_close(self, seat: int) -> Dict:
        seat = int(seat)
        if seat == 1:
            raise AgentError(ERR_ARGS, "seat 1 is permanent")
        be = self._seats.pop(seat, None)
        if be is not None:
            ui = getattr(be, "_ui", None)
            if ui is not None:
                try:
                    ui.close()
                except Exception:
                    pass
        return self.seats()

    def _seat(self, seat) -> BaseInput:
        try:
            seat = int(seat)
        except (TypeError, ValueError):
            raise AgentError(ERR_ARGS, "seat must be an integer")
        be = self._seats.get(seat)
        if be is None:
            raise AgentError(ERR_ARGS, f"seat {seat} not open (open seats: {sorted(self._seats)})")
        return be

    # mirror the BaseInput surface, with a seat selector
    def button(self, name, action, duration_ms=80, seat=1):
        return self._seat(seat).button(name, action, duration_ms)

    def combo(self, buttons, duration_ms=80, seat=1):
        return self._seat(seat).combo(buttons, duration_ms)

    def axis(self, name, value, seat=1):
        return self._seat(seat).axis(name, value)

    def type_text(self, text, seat=1):
        return self._seat(seat).type_text(text)


def make_input(force_mock: bool = False) -> SeatManager:
    def factory(seat: int = 1) -> BaseInput:
        # Per-seat device name: "AI virtual controller N" (N = 1..MAX_SEATS), so up
        # to four players (AI agents + a human guest) each get a distinctly-named pad
        # while the Xbox-360 IDENTITY (vendor/product/version → GUID) is shared/unchanged.
        name = "AI virtual controller %d" % int(seat)
        if not force_mock:
            try:
                if os.access("/dev/uinput", os.W_OK):
                    import evdev  # noqa: F401
                    pad = EvdevInput(name=name)
                    # Auto-register the seat pad's GUID with the launcher so a fresh
                    # state (no PT pad opened yet) still gets a correct es_input entry.
                    # Uses PT_KEYS (same as EvdevInput.cap now) so indices agree with
                    # any PT-pad entry for the same GUID; ensure_es_input_entry is
                    # idempotent and self-heals stale entries. Xbox-360 bustype = BUS_USB (3).
                    try:
                        if "GOSE_ES_INPUT_CFG" in os.environ or os.path.isdir(
                                os.path.dirname(ES_INPUT_CFG)):
                            cfg = os.environ.get("GOSE_ES_INPUT_CFG", ES_INPUT_CFG)
                            ensure_es_input_entry(name, 0x045e, 0x028e, 0x0110, 3, cfg)
                    except Exception as exc:
                        log.error("es_input auto-register failed for seat pad '%s': %s",
                                  name, exc)
                    return pad
            except Exception:
                pass
        return MockInput()
    return SeatManager(factory, force_mock=force_mock)
