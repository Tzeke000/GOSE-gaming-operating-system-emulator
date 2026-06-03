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

    def __init__(self):
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

    def __init__(self):
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
        self._ui = evdev.UInput(cap, name="GOSE Virtual Gamepad")
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


def make_input(force_mock: bool = False) -> BaseInput:
    if force_mock:
        return MockInput()
    try:
        if os.access("/dev/uinput", os.W_OK):
            import evdev  # noqa: F401
            return EvdevInput()
    except Exception:
        pass
    return MockInput()
