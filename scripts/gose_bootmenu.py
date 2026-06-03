#!/usr/bin/env python3
"""GOSE Boot Menu trigger logic + early-boot detector (mock-friendly).

The "BIOS"-style boot picker (see gui/mockup/bootmenu.html) is shown when the
trigger combo is held during a short window right after power-on — exactly like
tapping F12/DEL on a PC. Otherwise the default entry auto-boots after a timeout.

This module holds the *pure decision logic* (unit-tested in agent/tests) plus a
mock input source, so it runs in any container. Reading the real controller
(evdev) or GPIO is device-specific and marked [needs hardware]; wire it into
`read_buttons()` once the Odin 2 is in hand.

NOTE: this only decides *whether* to show the menu and *what was picked*.
Actually switching OS (ROCKNIX SD vs Batocera SD vs internal Android) on the
Odin 2 needs the one-time abl-mod + a fastboot "switch boot mode" — those
commands are [needs hardware] and live in scripts/setup-device.sh later.
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass

# Trigger combo uses logical button names so it stays controller-agnostic.
DEFAULT_COMBO = frozenset({"L1", "R1"})

# id, label, action — kept in sync with gui/mockup/bootmenu.html
BOOT_ENTRIES = [
    ("rocknix",  "ROCKNIX",          "boot:sd1"),
    ("batocera", "Batocera v42",     "boot:sd2"),
    ("android",  "Android",          "boot:internal"),
    ("recovery", "Recovery",         "tool:recovery"),
    ("safe",     "Safe Mode",        "tool:safe"),
    ("fastboot", "Fastboot / Flash", "tool:fastboot"),
    ("setup",    "GOSE Setup",       "tool:setup"),
    ("poweroff", "Power Off",        "tool:poweroff"),
]


@dataclass(frozen=True)
class BootConfig:
    combo: frozenset = DEFAULT_COMBO
    timeout_s: float = 5.0
    default_entry: str = "rocknix"


def decide(held, elapsed, cfg, *, menu_open=False):
    """Decide what to do during the boot window.

    Returns 'menu', 'boot:<entry-id>', or 'wait'. Extra buttons beyond the
    combo are fine; the combo just has to be a subset of what's held.
    """
    if menu_open:
        return "menu"
    if cfg.combo and cfg.combo.issubset(set(held)):
        return "menu"
    if elapsed >= cfg.timeout_s:
        return "boot:" + cfg.default_entry
    return "wait"


def move(index, delta, n):
    """Wrap-around menu navigation."""
    return (index + delta) % n


def select(index):
    """Action string for the entry at `index`."""
    return BOOT_ENTRIES[index][2]


def read_buttons():  # pragma: no cover - [needs hardware]
    """Real device: read held buttons from the controller evdev/GPIO.

    On the Odin 2 this is an early-boot read (systemd unit before the GUI, or an
    initramfs hook) of /dev/input/event* via python-evdev. Returns a set of
    logical names like {"L1", "R1"}. Not available in the container.
    """
    raise NotImplementedError("evdev/GPIO read is device-specific [needs hardware]")


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="GOSE Boot Menu trigger (mock).")
    ap.add_argument("--mock-hold", default="", help="comma list of held buttons, e.g. L1,R1")
    ap.add_argument("--elapsed", type=float, default=0.0, help="seconds since power-on")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--combo", default="L1,R1", help="trigger combo")
    ap.add_argument("--self-test", action="store_true", help="run internal assertions")
    a = ap.parse_args(argv)
    if a.self_test:
        cfg = BootConfig()
        assert decide({"L1", "R1"}, 0.1, cfg) == "menu"
        assert decide({"L1"}, 0.1, cfg) == "wait"
        assert decide(set(), 5.0, cfg) == "boot:rocknix"
        assert decide(set(), 99, cfg, menu_open=True) == "menu"
        assert move(0, -1, 8) == 7 and move(7, 1, 8) == 0
        assert select(0) == "boot:sd1"
        print("self-test OK")
        return 0
    held = {b.strip() for b in a.mock_hold.split(",") if b.strip()}
    combo = frozenset(b.strip() for b in a.combo.split(",") if b.strip())
    cfg = BootConfig(combo=combo, timeout_s=a.timeout)
    print(decide(held, a.elapsed, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
