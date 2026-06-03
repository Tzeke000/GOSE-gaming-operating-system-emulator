"""Tests for the GOSE platform + input-mode model (scripts/gose_input.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import gose_input as gi  # noqa: E402


class InputModel(unittest.TestCase):
    def test_defaults_per_platform(self):
        self.assertEqual(gi.default_mode(gi.DEVICE), gi.NATIVE)
        self.assertEqual(gi.default_mode(gi.PC), gi.KEYBOARD)

    def test_available_modes(self):
        self.assertEqual(gi.available_modes(gi.PC), [gi.KEYBOARD, gi.CONTROLLER])
        self.assertIn(gi.NATIVE, gi.available_modes(gi.DEVICE))

    def test_native_not_valid_on_pc(self):
        self.assertFalse(gi.is_valid(gi.PC, gi.NATIVE))
        self.assertTrue(gi.is_valid(gi.DEVICE, gi.NATIVE))

    def test_device_auto_accepts_peripherals(self):
        self.assertTrue(gi.auto_accepts_peripherals(gi.DEVICE))
        self.assertFalse(gi.auto_accepts_peripherals(gi.PC))

    def test_resolve_pc_default_keyboard(self):
        self.assertEqual(gi.resolve(gi.PC), gi.KEYBOARD)

    def test_resolve_pc_controller_when_connected(self):
        self.assertEqual(gi.resolve(gi.PC, gi.CONTROLLER, connected={gi.CONTROLLER}), gi.CONTROLLER)

    def test_resolve_pc_controller_falls_back_without_pad(self):
        self.assertEqual(gi.resolve(gi.PC, gi.CONTROLLER, connected=set()), gi.KEYBOARD)

    def test_resolve_device_default_native(self):
        self.assertEqual(gi.resolve(gi.DEVICE), gi.NATIVE)
        self.assertEqual(gi.resolve(gi.DEVICE, gi.CONTROLLER), gi.CONTROLLER)

    def test_resolve_uses_remembered(self):
        self.assertEqual(gi.resolve(gi.PC, remembered=gi.CONTROLLER, connected={gi.CONTROLLER}), gi.CONTROLLER)

    def test_resolve_ignores_invalid_request(self):
        self.assertEqual(gi.resolve(gi.PC, "native"), gi.KEYBOARD)

    def test_active_inputs_device_hotplugs(self):
        self.assertEqual(gi.active_inputs(gi.DEVICE, gi.NATIVE, {gi.CONTROLLER}),
                         {gi.NATIVE, gi.CONTROLLER})

    def test_active_inputs_pc_keyboard_only(self):
        self.assertEqual(gi.active_inputs(gi.PC, gi.KEYBOARD), {gi.KEYBOARD})


if __name__ == "__main__":
    unittest.main()
