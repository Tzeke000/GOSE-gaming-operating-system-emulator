"""Tests for the GOSE-PC OVA packager (pc-image/make_ova.py)."""
import os
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pc-image"))
import make_ova as ova  # noqa: E402


class OvfDescriptor(unittest.TestCase):
    def setUp(self):
        self.ovf = ova.build_ovf("GOSE-PC", "GOSE-PC-disk1.vmdk", 1234,
                                 32 * 1024**3, 6144, 4)

    def test_contains_name_and_disk(self):
        self.assertIn("<Name>GOSE-PC</Name>", self.ovf)
        self.assertIn('ovf:href="GOSE-PC-disk1.vmdk"', self.ovf)

    def test_memory_and_cpus(self):
        self.assertIn("<rasd:VirtualQuantity>6144</rasd:VirtualQuantity>", self.ovf)
        self.assertIn("<rasd:VirtualQuantity>4</rasd:VirtualQuantity>", self.ovf)

    def test_capacity_and_size(self):
        self.assertIn(f'ovf:capacity="{32 * 1024**3}"', self.ovf)
        self.assertIn('ovf:size="1234"', self.ovf)

    def test_streamoptimized_format(self):
        self.assertIn("streamOptimized", self.ovf)


class OvaPackaging(unittest.TestCase):
    def test_write_ova_members_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            ovf_path = os.path.join(d, "GOSE-PC.ovf")
            disk_path = os.path.join(d, "GOSE-PC-disk1.vmdk")
            with open(ovf_path, "w") as f:
                f.write(ova.build_ovf("GOSE-PC", "GOSE-PC-disk1.vmdk", 16, 1024, 2048, 2))
            with open(disk_path, "wb") as f:
                f.write(b"\0" * 16)
            out = os.path.join(d, "GOSE-PC.ova")
            ova.write_ova(out, ovf_path, disk_path)

            with tarfile.open(out) as tar:
                names = tar.getnames()
            # OVF descriptor must come first; disk + manifest present
            self.assertTrue(names[0].endswith(".ovf"))
            self.assertIn("GOSE-PC-disk1.vmdk", names)
            self.assertIn("GOSE-PC.mf", names)

    def test_manifest_has_sha256_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.bin")
            with open(p, "wb") as f:
                f.write(b"hello")
            mf = ova.build_manifest([p])
            self.assertTrue(mf.startswith("SHA256(x.bin)= "))
            self.assertEqual(len(mf.strip().split("= ")[1]), 64)


if __name__ == "__main__":
    unittest.main()
