"""Input capability: inject gamepad + keyboard events.

Real backend creates a virtual gamepad via Linux `uinput` (through python-evdev),
so emulators see the AI as just another controller. Mock backend records events
(used in CI / cloud container where /dev/uinput isn't available).
"""
from __future__ import annotations

import os
import time
from typing import Dict, List

from ..protocol import AgentError, ERR_ARGS, ERR_BACKEND

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
        cap = {
            e.EV_KEY: list(self._btn.values()),
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


class SeatManager:
    """Multiplayer seats: one virtual controller per seat (docs/16 + multiplayer plan).

    Seat 1 is created at startup (back-compat: it IS the original single pad).
    More seats open on demand up to MAX_SEATS; each evdev seat is its own uinput
    pad, so the OS/emulator sees one controller per player. Seat order == pad
    creation order == the player order `_virtual_pad_args` passes at game launch.
    """
    MAX_SEATS = 4

    def __init__(self, factory):
        self._factory = factory          # (seat:int) -> BaseInput
        self._seats: Dict[int, BaseInput] = {1: factory(1)}

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
                    return EvdevInput(name=name)
            except Exception:
                pass
        return MockInput()
    return SeatManager(factory)
