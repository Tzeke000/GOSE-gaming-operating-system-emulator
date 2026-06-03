"""Validate the GOSE EmulationStation theme (well-formed XML + expected structure)."""
import os
import unittest
import xml.etree.ElementTree as ET

THEME = os.path.join(os.path.dirname(__file__), "..", "..",
                     "pc-image", "gose-layer", "themes", "gose")


class GoseTheme(unittest.TestCase):
    def setUp(self):
        self.tree = ET.parse(os.path.join(THEME, "theme.xml"))
        self.root = self.tree.getroot()

    def test_well_formed_and_root(self):
        self.assertEqual(self.root.tag, "theme")

    def test_format_version_7(self):
        self.assertEqual(self.root.findtext("formatVersion"), "7")

    def test_has_core_views(self):
        names = ",".join(v.get("name", "") for v in self.root.findall("view"))
        for v in ("system", "basic", "detailed"):
            self.assertIn(v, names)

    def test_references_existing_assets(self):
        for rel in ("art/background.png", "art/logo.png",
                    "fonts/Inter-700.ttf", "fonts/Inter-600.ttf"):
            self.assertTrue(os.path.exists(os.path.join(THEME, rel)), rel)

    def test_gamelist_uses_accent_selector(self):
        xml = ET.tostring(self.root, encoding="unicode")
        self.assertIn("5CD0FF", xml)   # GOSE accent present
        self.assertIn("systemcarousel", xml)
        self.assertIn("gamelist", xml)


if __name__ == "__main__":
    unittest.main()
