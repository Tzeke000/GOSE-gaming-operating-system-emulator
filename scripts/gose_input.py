#!/usr/bin/env python3
"""GOSE platform + input-mode model (shared by the device and the PC app).

GOSE runs in two places:
  - "device": the AYN Odin 2 (handheld). Default navigation = NATIVE controls;
    it also AUTO-ACCEPTS any peripheral that connects (keyboard or controller,
    Bluetooth/dongle/wired) on top of native.
  - "pc": the downloadable desktop app. Default navigation = KEYBOARD & mouse;
    the user can switch to CONTROLLER instead.

Pure logic, unit-tested in agent/tests (no hardware). The web UI mirrors this in
assets/platform.js; real peripheral enumeration on each OS is [needs hardware].
"""
from __future__ import annotations
import argparse
import sys

DEVICE, PC = "device", "pc"
NATIVE, KEYBOARD, CONTROLLER = "native", "keyboard", "controller"

DEFAULT_MODE = {DEVICE: NATIVE, PC: KEYBOARD}
AVAILABLE = {DEVICE: [NATIVE, CONTROLLER, KEYBOARD], PC: [KEYBOARD, CONTROLLER]}


def default_mode(platform):
    return DEFAULT_MODE[platform]


def available_modes(platform):
    return list(AVAILABLE[platform])


def is_valid(platform, mode):
    return mode in AVAILABLE[platform]


def auto_accepts_peripherals(platform):
    """The device hot-plugs peripherals alongside native; the PC app does not."""
    return platform == DEVICE


def resolve(platform, requested=None, *, remembered=None, connected=()):
    """Pick the active input mode.

    Priority: an explicit valid `requested` > a valid `remembered` > the platform
    default. On a PC, choosing CONTROLLER with none connected falls back to
    KEYBOARD so the user is never stranded.
    """
    connected = set(connected)
    mode = None
    for cand in (requested, remembered):
        if cand and is_valid(platform, cand):
            mode = cand
            break
    if mode is None:
        mode = default_mode(platform)
    if platform == PC and mode == CONTROLLER and CONTROLLER not in connected:
        return KEYBOARD
    return mode


def active_inputs(platform, mode, connected=()):
    """Every input the OS will listen to right now (drives the hint bar)."""
    connected = set(connected)
    inputs = set()
    if platform == DEVICE:
        inputs.add(NATIVE)  # built-in controls are always live
        if auto_accepts_peripherals(platform):
            inputs |= connected & {KEYBOARD, CONTROLLER}
    inputs.add(mode)
    return inputs


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="GOSE input-mode resolver (mock).")
    ap.add_argument("--platform", choices=[DEVICE, PC], default=PC)
    ap.add_argument("--requested", default=None)
    ap.add_argument("--remembered", default=None)
    ap.add_argument("--connected", default="", help="comma list: controller,keyboard")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        assert default_mode(DEVICE) == NATIVE and default_mode(PC) == KEYBOARD
        assert available_modes(PC) == [KEYBOARD, CONTROLLER]
        assert NATIVE in available_modes(DEVICE) and not is_valid(PC, NATIVE)
        assert auto_accepts_peripherals(DEVICE) and not auto_accepts_peripherals(PC)
        assert resolve(PC) == KEYBOARD
        assert resolve(PC, CONTROLLER, connected={CONTROLLER}) == CONTROLLER
        assert resolve(PC, CONTROLLER, connected=set()) == KEYBOARD
        assert resolve(DEVICE) == NATIVE and resolve(DEVICE, CONTROLLER) == CONTROLLER
        assert resolve(PC, remembered=CONTROLLER, connected={CONTROLLER}) == CONTROLLER
        assert active_inputs(DEVICE, NATIVE, {CONTROLLER}) == {NATIVE, CONTROLLER}
        assert active_inputs(PC, KEYBOARD) == {KEYBOARD}
        print("self-test OK")
        return 0
    connected = {c.strip() for c in a.connected.split(",") if c.strip()}
    print(resolve(a.platform, a.requested, remembered=a.remembered, connected=connected))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
