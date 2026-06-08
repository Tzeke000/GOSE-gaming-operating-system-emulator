"""Tests for the play-map registry (#117)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gose_agent.capabilities.playmap import PlayMapRegistry, load_play_maps  # noqa: E402
from gose_agent.protocol import AgentError  # noqa: E402

_VALID_MAP = {
    "id": "testgame",
    "name": "Test Game",
    "system": "nes",
    "crc": "deadbeef",
    "controls": {
        "up":   {"input": "up",   "effect": "move paddle up"},
        "down": {"input": "down", "effect": "move paddle down"},
    },
    "ram_fields": {
        "ball_x": {"address": "0x07", "type": "u8"},
        "score":  {"address": "0x14", "type": "u8"},
    },
    "game_flow": {
        "serve_detect": "ball_x != 0",
        "game_over": "score >= 9",
    },
    "seats": {"ai_default": 2},
}


class TestLoadPlayMaps(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _write(self, name, data):
        with open(os.path.join(self.d, name), "w") as fh:
            json.dump(data, fh)

    def test_loads_valid_map(self):
        self._write("testgame.json", _VALID_MAP)
        maps, skipped = load_play_maps(self.d)
        self.assertIn("testgame", maps)
        self.assertEqual(skipped, {})

    def test_skips_missing_required_key(self):
        bad = dict(_VALID_MAP)
        del bad["controls"]
        self._write("bad.json", bad)
        maps, skipped = load_play_maps(self.d)
        self.assertNotIn("bad", maps)
        self.assertIn("bad.json", skipped)
        self.assertIn("controls", skipped["bad.json"])

    def test_skips_invalid_json(self):
        with open(os.path.join(self.d, "broken.json"), "w") as fh:
            fh.write("{not valid json")
        maps, skipped = load_play_maps(self.d)
        self.assertNotIn("broken", maps)
        self.assertIn("broken.json", skipped)

    def test_skips_wrong_controls_type(self):
        bad = dict(_VALID_MAP, controls="not-a-dict")
        self._write("wrongtype.json", bad)
        _, skipped = load_play_maps(self.d)
        self.assertIn("wrongtype.json", skipped)

    def test_empty_dir(self):
        maps, skipped = load_play_maps(self.d)
        self.assertEqual(maps, {})
        self.assertEqual(skipped, {})

    def test_missing_dir(self):
        maps, skipped = load_play_maps("/nonexistent/path/that/cannot/exist")
        self.assertEqual(maps, {})


class TestPlayMapRegistry(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "testgame.json"), "w") as fh:
            json.dump(_VALID_MAP, fh)

    def test_list_maps_includes_loaded(self):
        reg = PlayMapRegistry(self.d)
        out = reg.list_maps()
        self.assertIn("testgame", out["play_maps"])
        self.assertEqual(out["count"], 1)

    def test_list_maps_summary_fields(self):
        reg = PlayMapRegistry(self.d)
        summary = reg.list_maps()["play_maps"]["testgame"]
        self.assertEqual(summary["id"], "testgame")
        self.assertEqual(summary["name"], "Test Game")
        self.assertEqual(summary["system"], "nes")
        self.assertIn("up", summary["controls"])

    def test_get_map_returns_full_data(self):
        reg = PlayMapRegistry(self.d)
        m = reg.get_map("testgame")
        self.assertEqual(m["id"], "testgame")
        self.assertIn("ball_x", m["ram_fields"])
        self.assertIn("game_flow", m)

    def test_get_map_unknown_raises(self):
        reg = PlayMapRegistry(self.d)
        with self.assertRaises(AgentError) as ctx:
            reg.get_map("no_such_game")
        self.assertIn("no_such_game", ctx.exception.message)
        self.assertIn("testgame", ctx.exception.message)

    def test_skipped_visible_in_list(self):
        with open(os.path.join(self.d, "broken.json"), "w") as fh:
            fh.write("{not json")
        reg = PlayMapRegistry(self.d)
        out = reg.list_maps()
        self.assertIn("broken.json", out["skipped"])


class TestPong1K2PBakedMap(unittest.TestCase):
    """Smoke-test the actual baked pong1k2p.json against the schema."""

    BAKED_DIR = os.path.join(os.path.dirname(__file__), "..", "gose_agent", "play_maps")

    def test_pong_map_loads(self):
        if not os.path.isdir(self.BAKED_DIR):
            self.skipTest("play_maps dir not found")
        reg = PlayMapRegistry(self.BAKED_DIR)
        self.assertIn("pong1k2p", reg.maps, "pong1k2p.json must be present in play_maps/")

    def test_pong_map_has_expected_fields(self):
        if not os.path.isdir(self.BAKED_DIR):
            self.skipTest("play_maps dir not found")
        reg = PlayMapRegistry(self.BAKED_DIR)
        m = reg.get_map("pong1k2p")
        # Controls: must have up and down
        self.assertIn("up",   m["controls"])
        self.assertIn("down", m["controls"])
        # RAM fields: the verified addresses
        rf = m["ram_fields"]
        self.assertIn("ball_x",      rf)
        self.assertIn("ball_y",      rf)
        self.assertIn("p1_paddle_y", rf)
        self.assertIn("p2_paddle_y", rf)
        self.assertIn("score_left",  rf)
        self.assertIn("score_right", rf)
        # Game flow: serve detect + game-over rule
        gf = m["game_flow"]
        self.assertIn("serve_detect", gf)
        self.assertIn("game_over",    gf)
        # Seats: ai_default is 2 (RIGHT paddle = js0)
        self.assertEqual(m["seats"]["ai_default"], 2)

    def test_pong_map_addresses_match_nci_profile(self):
        """Addresses in the play-map must agree with the NCI profile."""
        if not os.path.isdir(self.BAKED_DIR):
            self.skipTest("play_maps dir not found")
        profile_path = os.path.join(
            os.path.dirname(__file__), "..", "gose_agent", "profiles", "pong1k2p.json")
        if not os.path.isfile(profile_path):
            self.skipTest("pong1k2p NCI profile not found")
        with open(profile_path) as fh:
            profile = json.load(fh)
        profile_addrs = {f["name"]: f["address"] for f in profile["fields"]}

        reg = PlayMapRegistry(self.BAKED_DIR)
        m = reg.get_map("pong1k2p")
        for field_name, pm_info in m["ram_fields"].items():
            if field_name in profile_addrs:
                self.assertEqual(
                    pm_info["address"], profile_addrs[field_name],
                    f"play-map address for '{field_name}' disagrees with NCI profile")


if __name__ == "__main__":
    unittest.main()
