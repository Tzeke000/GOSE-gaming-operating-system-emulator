import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gose_agent import protocol as P  # noqa: E402


class TestProtocol(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        msg = {"id": 1, "op": "ping", "args": {"x": 2}}
        line = P.encode(msg).decode("utf-8")
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(P.decode_line(line), msg)

    def test_decode_bad_json_raises(self):
        with self.assertRaises(P.AgentError) as ctx:
            P.decode_line("{not json")
        self.assertEqual(ctx.exception.code, P.ERR_BADREQ)

    def test_decode_non_object_raises(self):
        with self.assertRaises(P.AgentError):
            P.decode_line("[1,2,3]")

    def test_decode_empty_raises(self):
        with self.assertRaises(P.AgentError):
            P.decode_line("   ")

    def test_response_helpers(self):
        ok = P.ok_response(7, {"a": 1})
        self.assertEqual(ok, {"id": 7, "ok": True, "result": {"a": 1}})
        err = P.err_response(7, P.ERR_AUTH, "nope")
        self.assertEqual(err["ok"], False)
        self.assertEqual(err["code"], P.ERR_AUTH)
        ev = P.event("game.launched", {"pid": 1})
        self.assertEqual(ev["event"], "game.launched")


if __name__ == "__main__":
    unittest.main()
