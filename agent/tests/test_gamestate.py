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

    def __init__(self, memory=None, status="PLAYING n64,super mario 64,abcd1234"):
        self.mem = bytearray(memory or bytes(0x10000))
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
        if cmd in ("READ_CORE_MEMORY", "READ_CORE_RAM"):
            a = int(parts[1], 16); n = int(parts[2])
            hexb = " ".join(f"{b:02x}" for b in self.mem[a:a + n])
            return f"{cmd} {parts[1]} {hexb}"
        if cmd in ("WRITE_CORE_MEMORY", "WRITE_CORE_RAM"):
            a = int(parts[1], 16)
            vals = [int(b, 16) for b in parts[2:]]
            self.mem[a:a + len(vals)] = bytes(vals)
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


class TestStableRetroImporter(unittest.TestCase):
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
