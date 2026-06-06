import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gose_agent.capabilities.gamestate import (  # noqa: E402
    GameStateCapability, resolve_type, GameProfile,
)
from gose_agent.protocol import AgentError  # noqa: E402


class MockRetroArch:
    """Tiny UDP server mimicking RetroArch's Network Command Interface."""

    def __init__(self, memory=None, status="PLAYING n64,super mario 64,abcd1234",
                 ram=None):
        self.mem = bytearray(memory or bytes(0x10000))
        # Optional separate buffer for the *_CORE_RAM verbs (the achievement
        # address space). Default: same buffer as core_memory.
        self.ram = bytearray(ram) if ram is not None else self.mem
        self.status_line = status
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self._run = True
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        self.sock.settimeout(0.2)
        while self._run:
            try:
                data, addr = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            parts = data.decode().split()
            cmd = parts[0] if parts else ""
            resp = self._handle(cmd, parts)
            if resp is not None:
                self.sock.sendto(resp.encode(), addr)

    def _handle(self, cmd, parts):
        if cmd == "GET_STATUS":
            return f"GET_STATUS {self.status_line}"
        buf = self.ram if cmd.endswith("_CORE_RAM") else self.mem
        if cmd in ("READ_CORE_MEMORY", "READ_CORE_RAM"):
            a = int(parts[1], 16); n = int(parts[2])
            hexb = " ".join(f"{b:02x}" for b in buf[a:a + n])
            return f"{cmd} {parts[1]} {hexb}"
        if cmd in ("WRITE_CORE_MEMORY", "WRITE_CORE_RAM"):
            a = int(parts[1], 16)
            vals = [int(b, 16) for b in parts[2:]]
            buf[a:a + len(vals)] = bytes(vals)
            return f"{cmd} {parts[1]} {len(vals)}"
        return f"{cmd} -1 unknown"

    def close(self):
        self._run = False
        self.sock.close()


class TestTypeResolution(unittest.TestCase):
    def test_aliases_and_descriptors(self):
        self.assertEqual(resolve_type("u16", "little"), ("<", "H", 2))
        self.assertEqual(resolve_type("u16", "big"), (">", "H", 2))
        self.assertEqual(resolve_type(">u4", "little"), (">", "I", 4))
        self.assertEqual(resolve_type("<i2", "big"), ("<", "h", 2))
        self.assertEqual(resolve_type("|u1", "big"), ("<", "B", 1))  # endian irrelevant
        self.assertEqual(resolve_type("float32", "big"), (">", "f", 4))

    def test_bad_type(self):
        with self.assertRaises(AgentError):
            resolve_type("u3", "little")

    def test_bcd_descriptors_and_aliases(self):
        self.assertEqual(resolve_type(">n6", "little"), (">", "bcd_n", 6))
        self.assertEqual(resolve_type(">d2", "little"), (">", "bcd_d", 2))
        self.assertEqual(resolve_type("<d2", "big"), ("<", "bcd_d", 2))
        self.assertEqual(resolve_type("|d1", "big"), ("<", "bcd_d", 1))  # 1 byte: endian irrelevant
        # bcd_* aliases carry no endianness -> profile default applies
        self.assertEqual(resolve_type("bcd_n6", "big"), (">", "bcd_n", 6))
        self.assertEqual(resolve_type("bcd_d2", "little"), ("<", "bcd_d", 2))

    def test_bad_bcd(self):
        with self.assertRaises(AgentError):
            resolve_type("bcd_x4", "little")
        with self.assertRaises(AgentError):
            resolve_type(">n0", "little")
        with self.assertRaises(AgentError):
            resolve_type(">d99", "little")


class TestBcdDecode(unittest.TestCase):
    @staticmethod
    def _decode(type_str, raw, endian="little"):
        p = GameProfile({"name": "t", "endian": endian, "fields": [
            {"name": "v", "address": 0, "type": type_str}]})
        return p.decode_field(p.fields[0], raw)

    def test_nybble_bcd_big_endian(self):
        # SMB score ">n6": one digit per byte (low nybble), MSB digit first.
        self.assertEqual(self._decode(">n6", bytes([0, 1, 2, 3, 4, 5])), 12345)
        # High nybbles must be ignored.
        self.assertEqual(
            self._decode(">n6", bytes([0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5])), 12345)

    def test_nybble_bcd_little_endian(self):
        self.assertEqual(self._decode("<n6", bytes([0, 1, 2, 3, 4, 5])), 543210)

    def test_packed_bcd(self):
        self.assertEqual(self._decode(">d2", bytes([0x12, 0x34])), 1234)
        self.assertEqual(self._decode("<d2", bytes([0x34, 0x12])), 1234)
        self.assertEqual(self._decode("|d1", bytes([0x42])), 42)

    def test_bcd_alias_uses_profile_endian(self):
        raw = bytes([0x12, 0x34])
        self.assertEqual(self._decode("bcd_d2", raw, endian="big"), 1234)
        self.assertEqual(self._decode("bcd_d2", raw, endian="little"), 3412)


class TestGameStateOverMock(unittest.TestCase):
    def setUp(self):
        self.ra = MockRetroArch()
        # Seed memory: big-endian float 1.5 at 0x100, u16=4660 (0x1234 BE) at 0x200.
        self.ra.mem[0x100:0x104] = struct.pack(">f", 1.5)
        self.ra.mem[0x200:0x202] = struct.pack(">H", 4660)
        self.ra.mem[0x300] = 7
        self.profiles_dir = tempfile.mkdtemp()
        profile = {
            "name": "Test Game", "system": "n64", "endian": "big",
            "read_method": "core_memory",
            "match": {"game_substr": "super mario 64"},
            "fields": [
                {"name": "x", "address": "0x100", "type": "float32"},
                {"name": "score", "address": "0x200", "type": "u16"},
                {"name": "lives", "address": "0x300", "type": "u8"},
            ],
        }
        with open(os.path.join(self.profiles_dir, "testgame.json"), "w") as fh:
            json.dump(profile, fh)
        self.cap = GameStateCapability(self.profiles_dir, "127.0.0.1", self.ra.port)

    def tearDown(self):
        self.ra.close()

    def test_list_profiles(self):
        out = self.cap.list_profiles()
        self.assertIn("testgame", out["profiles"])

    def test_read_decodes_memory(self):
        self.cap.attach("testgame")
        r = self.cap.read()
        self.assertAlmostEqual(r["fields"]["x"], 1.5, places=4)
        self.assertEqual(r["fields"]["score"], 4660)
        self.assertEqual(r["fields"]["lives"], 7)

    def test_attach_autodetect(self):
        out = self.cap.attach()  # uses GET_STATUS -> "super mario 64"
        self.assertEqual(out["attached"], "testgame")
        self.assertTrue(out["detected"])

    def test_read_without_attach_errors(self):
        with self.assertRaises(AgentError):
            self.cap.read()

    def test_raw_read_write_roundtrip(self):
        self.cap.write_raw("0x400", [0xde, 0xad, 0xbe, 0xef])
        r = self.cap.read_raw("0x400", 4)
        self.assertEqual(r["hex"], "deadbeef")

    def test_status(self):
        s = self.cap.status()
        self.assertEqual(s["state"], "PLAYING")
        self.assertIn("mario", (s["game"] or "").lower())


class TestPerFieldReadMethod(unittest.TestCase):
    def test_field_read_method_overrides_profile(self):
        # Distinct buffers for core_memory vs core_ram so routing is observable.
        ra = MockRetroArch(ram=bytes(0x1000))
        ra.mem[0x10] = 11
        ra.ram[0x10] = 22
        profiles_dir = tempfile.mkdtemp()
        profile = {
            "name": "Routing Test", "read_method": "core_memory",
            "match": {"game_substr": "routing"},
            "fields": [
                {"name": "via_memory", "address": "0x10", "type": "u8"},
                {"name": "via_ram", "address": "0x10", "type": "u8",
                 "read_method": "core_ram"},
            ],
        }
        with open(os.path.join(profiles_dir, "routing.json"), "w") as fh:
            json.dump(profile, fh)
        cap = GameStateCapability(profiles_dir, "127.0.0.1", ra.port)
        try:
            cap.attach("routing")
            r = cap.read()
            self.assertEqual(r["fields"]["via_memory"], 11)
            self.assertEqual(r["fields"]["via_ram"], 22)
        finally:
            ra.close()


class TestProfileSkipsAreVisible(unittest.TestCase):
    def test_bad_profiles_logged_and_listed(self):
        d = tempfile.mkdtemp()
        good = {"name": "Good", "match": {"game_substr": "good"},
                "fields": [{"name": "lives", "address": 0, "type": "u8"}]}
        with open(os.path.join(d, "good.json"), "w") as fh:
            json.dump(good, fh)
        with open(os.path.join(d, "broken.json"), "w") as fh:
            fh.write("{not valid json")
        bad_type = {"name": "BadType",
                    "fields": [{"name": "x", "address": 0, "type": "u3"}]}
        with open(os.path.join(d, "badtype.json"), "w") as fh:
            json.dump(bad_type, fh)
        with self.assertLogs("gose.agent.gamestate", level="WARNING") as cm:
            cap = GameStateCapability(d, "127.0.0.1", 1)  # no I/O at init
        out = cap.list_profiles()
        self.assertIn("good", out["profiles"])
        self.assertNotIn("broken", out["profiles"])
        self.assertEqual(sorted(out["skipped"]), ["badtype.json", "broken.json"])
        self.assertIn("unsupported type 'u3'", out["skipped"]["badtype.json"])
        joined = "\n".join(cm.output)
        self.assertIn("broken.json", joined)
        self.assertIn("badtype.json", joined)


class TestStableRetroImporter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

    def test_translate_address_vectors(self):
        from import_stable_retro import translate_address  # noqa: E402
        # Verified examples (system-bus -> READ_CORE_RAM/achievement space):
        self.assertEqual(translate_address("nes", 0x75A), 0x75A)          # SMB lives
        self.assertEqual(translate_address("snes", 0x7E0DBE), 0xDBE)      # SMW lives
        self.assertEqual(translate_address("genesis", 0xFFFE10), 0xFE10)  # Sonic zone
        self.assertEqual(translate_address("atari2600", 0x95), 0x15)      # RIOT RAM
        self.assertEqual(translate_address("gameboy", 0xDC0B), 0xDC0B)    # identity (rcheevos)
        self.assertEqual(translate_address("sms", 0xD246), 0x1246)        # Z80 work RAM
        # Outside the console's RA-mappable RAM window -> None (core_memory fallback).
        self.assertIsNone(translate_address("nes", 0x6000))
        self.assertIsNone(translate_address("snes", 0x3000))
        self.assertIsNone(translate_address("genesis", 0x100))
        self.assertIsNone(translate_address("atari2600", 0x10))
        self.assertIsNone(translate_address("gameboy", 0x10000))
        self.assertIsNone(translate_address("", 0x10))  # unknown system

    def test_convert_translates_bus_addresses(self):
        from import_stable_retro import convert  # noqa: E402
        game_dir = os.path.join(tempfile.mkdtemp(), "SuperMarioWorld-Snes")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "data.json"), "w") as fh:
            json.dump({"info": {
                "lives": {"address": 0x7E0DBE, "type": "|u1"},
                "weird": {"address": 0x3000, "type": "|u1"},   # outside work RAM
            }}, fh)
        prof = convert(game_dir)  # no metadata.json -> system from dir name
        self.assertEqual(prof["system"], "Snes")
        self.assertEqual(prof["read_method"], "core_ram")
        names = {f["name"]: f for f in prof["fields"]}
        self.assertEqual(names["lives"]["address"], 0xDBE)     # translated
        self.assertNotIn("read_method", names["lives"])
        self.assertEqual(names["weird"]["address"], 0x3000)    # bus addr kept
        self.assertEqual(names["weird"]["read_method"], "core_memory")
        GameProfile(prof)  # still loadable

    def test_convert_unknown_system_falls_back(self):
        from import_stable_retro import convert  # noqa: E402
        game_dir = os.path.join(tempfile.mkdtemp(), "SomeGame-NintendoDS-v0")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "data.json"), "w") as fh:
            json.dump({"info": {"hp": {"address": 0x21000, "type": "<u2"}}}, fh)
        prof = convert(game_dir)
        self.assertEqual(prof["system"], "NintendoDS")
        self.assertEqual(prof["read_method"], "core_memory")
        names = {f["name"]: f for f in prof["fields"]}
        self.assertEqual(names["hp"]["address"], 0x21000)      # untranslated bus addr
        self.assertNotIn("read_method", names["hp"])
        GameProfile(prof)

    def test_convert_bcd_types_load_and_decode(self):
        from import_stable_retro import convert  # noqa: E402
        game_dir = os.path.join(tempfile.mkdtemp(), "SuperMarioBros-Nes")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "data.json"), "w") as fh:
            json.dump({"info": {
                "lives": {"address": 1882, "type": "|u1"},       # NES $075A
                "score": {"address": 2013, "type": ">n6"},       # SMB BCD score
            }}, fh)
        prof = convert(game_dir)
        names = {f["name"]: f for f in prof["fields"]}
        self.assertEqual(names["lives"]["address"], 0x75A)       # NES identity
        gp = GameProfile(prof)
        self.assertEqual(gp.field_size(names["score"]), 6)
        self.assertEqual(gp.decode_field(names["score"],
                                         bytes([0, 1, 2, 3, 4, 5])), 12345)

    def test_convert(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from import_stable_retro import convert  # noqa: E402
        gdir = tempfile.mkdtemp()
        os.rename(gdir, gdir)  # keep name
        game_dir = os.path.join(os.path.dirname(gdir), "Cool-Game-Genesis")
        os.makedirs(game_dir, exist_ok=True)
        with open(os.path.join(game_dir, "data.json"), "w") as fh:
            json.dump({"info": {"score": {"address": 128, "type": ">u4"},
                                "lives": {"address": 1234, "type": "|u1"}}}, fh)
        with open(os.path.join(game_dir, "metadata.json"), "w") as fh:
            json.dump({"system": "Genesis"}, fh)
        prof = convert(game_dir)
        self.assertEqual(prof["read_method"], "core_ram")
        self.assertEqual(prof["system"], "Genesis")
        names = {f["name"]: f for f in prof["fields"]}
        self.assertEqual(names["score"]["type"], ">u4")
        self.assertEqual(names["score"]["address"], 128)
        # Imported profile must be loadable + decodable by GameProfile.
        gp = GameProfile(prof)
        self.assertEqual(gp.field_size(names["score"]), 4)


if __name__ == "__main__":
    unittest.main()
