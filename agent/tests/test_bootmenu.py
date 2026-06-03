"""Tests for the GOSE Boot Menu trigger logic (scripts/gose_bootmenu.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import gose_bootmenu as bm  # noqa: E402


class BootMenuLogic(unittest.TestCase):
    def setUp(self):
        self.cfg = bm.BootConfig()

    def test_combo_opens_menu(self):
        self.assertEqual(bm.decide({"L1", "R1"}, 0.2, self.cfg), "menu")

    def test_partial_combo_waits(self):
        self.assertEqual(bm.decide({"L1"}, 0.2, self.cfg), "wait")

    def test_timeout_autoboots_default(self):
        self.assertEqual(bm.decide(set(), 5.0, self.cfg), "boot:rocknix")

    def test_before_timeout_waits(self):
        self.assertEqual(bm.decide(set(), 1.0, self.cfg), "wait")

    def test_menu_stays_open_once_shown(self):
        self.assertEqual(bm.decide(set(), 99, self.cfg, menu_open=True), "menu")

    def test_extra_buttons_still_trigger(self):
        self.assertEqual(bm.decide({"L1", "R1", "A"}, 0.1, self.cfg), "menu")

    def test_custom_combo(self):
        cfg = bm.BootConfig(combo=frozenset({"VOL_DOWN"}))
        self.assertEqual(bm.decide({"VOL_DOWN"}, 0.1, cfg), "menu")
        self.assertEqual(bm.decide({"L1", "R1"}, 0.1, cfg), "wait")

    def test_nav_wraps_both_ways(self):
        n = len(bm.BOOT_ENTRIES)
        self.assertEqual(bm.move(0, -1, n), n - 1)
        self.assertEqual(bm.move(n - 1, 1, n), 0)

    def test_select_returns_action(self):
        self.assertEqual(bm.select(0), "boot:sd")
        self.assertEqual(bm.select(len(bm.BOOT_ENTRIES) - 1), "tool:poweroff")

    def test_entries_are_rocknix_and_android_only(self):
        boot = [e for e in bm.BOOT_ENTRIES if e[2].startswith("boot:")]
        self.assertEqual([e[0] for e in boot], ["rocknix", "android"])
        self.assertEqual(len(bm.BOOT_ENTRIES), 7)


if __name__ == "__main__":
    unittest.main()
