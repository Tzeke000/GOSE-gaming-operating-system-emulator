"""Tests for the GOSE-PC VM launcher (scripts/gose_vm.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import gose_vm as vm  # noqa: E402


class AccelDetect(unittest.TestCase):
    def test_linux_with_kvm(self):
        self.assertEqual(vm.detect_accel("linux", has_kvm=True), "kvm")

    def test_linux_without_kvm(self):
        self.assertEqual(vm.detect_accel("linux", has_kvm=False), "tcg")

    def test_mac_and_windows(self):
        self.assertEqual(vm.detect_accel("darwin"), "hvf")
        self.assertEqual(vm.detect_accel("win32"), "whpx")

    def test_unknown_falls_back_to_tcg(self):
        self.assertEqual(vm.detect_accel("plan9"), "tcg")


class QemuCommand(unittest.TestCase):
    def test_basics(self):
        cmd = vm.build_qemu_cmd(vm.VmConfig(), "kvm")
        self.assertEqual(cmd[0], "qemu-system-x86_64")
        joined = " ".join(cmd)
        self.assertIn("accel=kvm", joined)
        self.assertIn("hostfwd=tcp::8731-:8731", joined)  # host 8731 -> guest agent 8731 (agent default; matches the GOSE MCP server)
        self.assertIn("hostfwd=tcp::2222-:22", joined)     # host 2222 -> guest ssh (layer injection on Windows)

    def test_cpu_host_only_when_accelerated(self):
        self.assertIn("host", vm.build_qemu_cmd(vm.VmConfig(), "kvm"))
        self.assertIn("qemu64", vm.build_qemu_cmd(vm.VmConfig(), "tcg"))

    def test_gpu_accelerated_display(self):
        self.assertIn("virtio-vga-gl", vm.build_qemu_cmd(vm.VmConfig(gpu=True), "kvm"))

    def test_headless(self):
        self.assertIn("none", vm.build_qemu_cmd(vm.VmConfig(headless=True), "tcg"))

    def test_shared_folder(self):
        cmd = vm.build_qemu_cmd(vm.VmConfig(share_dir="/roms"), "kvm")
        self.assertIn("virtio-9p-pci,fsdev=fs0,mount_tag=gose-share", cmd)

    def test_controller_passthrough(self):
        cmd = vm.build_qemu_cmd(vm.VmConfig(usb_controllers=["046d:c21d"]), "kvm")
        self.assertTrue(any("usb-host" in c and "0x046d" in c for c in cmd))


if __name__ == "__main__":
    unittest.main()
