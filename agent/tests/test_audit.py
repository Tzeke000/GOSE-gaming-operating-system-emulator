"""Audit log (guest-AI op recording) + pre-auth pairing-request tests.

audit_append is a separable function — unit-test format + rotation directly.
Then a live roundtrip proves: per-AI-token ops (allowed AND denied) land in the
audit file, dev-token ops do NOT, and an unauthenticated 'pair.request' forwards
to the (stubbed) UI server without granting anything.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))

from gose_agent.config import AgentConfig  # noqa: E402
from gose_agent import server as srv  # noqa: E402
from gose_client import GoseClient, GoseClientError  # noqa: E402


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestAuditAppend(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.unlink(self.path)   # start with NO file — first append must create it

    def tearDown(self):
        for p in (self.path, self.path + ".1"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def _lines(self, path=None):
        with open(path or self.path, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh.read().splitlines() if l]

    def test_creates_file_and_writes_record(self):
        srv.audit_append("Wren", "ping", True, path=self.path)
        (rec,) = self._lines()
        self.assertEqual(rec["name"], "Wren")
        self.assertEqual(rec["op"], "ping")
        self.assertIs(rec["ok"], True)
        self.assertNotIn("code", rec)
        self.assertAlmostEqual(rec["ts"], time.time(), delta=5)

    def test_denied_records_code(self):
        srv.audit_append("TestAI", "system.run", False, code="ERR_DENIED", path=self.path)
        (rec,) = self._lines()
        self.assertIs(rec["ok"], False)
        self.assertEqual(rec["code"], "ERR_DENIED")

    def test_appends_jsonl(self):
        for i in range(3):
            srv.audit_append("A", "op%d" % i, True, path=self.path)
        self.assertEqual([r["op"] for r in self._lines()], ["op0", "op1", "op2"])

    def test_rotation_past_max(self):
        # fill past the cap, then one more append must rotate to .1 + start fresh
        with open(self.path, "w") as fh:
            fh.write("x" * (srv._AUDIT_MAX + 1))
        srv.audit_append("A", "after.rotate", True, path=self.path)
        self.assertTrue(os.path.exists(self.path + ".1"))
        recs = self._lines()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["op"], "after.rotate")

    def test_failure_is_swallowed(self):
        # an unwritable path must not raise — audit can never take down dispatch
        srv.audit_append("A", "op", True, path=os.path.join(self.path, "no", "such", "dir.jsonl"))


class _StubPairServer:
    """Tiny HTTP stub standing in for the UI server's /ai/request."""

    def __init__(self):
        import http.server
        self.requests = []
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                stub.requests.append(json.loads(self.rfile.read(n).decode()))
                body = json.dumps({"ok": True, "name": stub.requests[-1].get("name"),
                                   "tier": stub.requests[-1].get("tier"), "pending": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.port = _free_port()
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()


class TestAuditRoundtrip(unittest.TestCase):
    """Real server, real sockets: who gets audited and who doesn't."""

    @classmethod
    def setUpClass(cls):
        fd, cls.tokens_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(cls.tokens_path, "w") as fh:
            json.dump({"ai-tok": {"name": "TestAI", "tier": "observe"}}, fh)
        fd, cls.audit_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.unlink(cls.audit_path)
        cls.stub = _StubPairServer()
        cls.patches = [
            mock.patch.object(srv, "_AI_TOKENS_PATH", cls.tokens_path),
            mock.patch.object(srv, "_AUDIT_PATH", cls.audit_path),
            mock.patch.object(srv, "_PAIR_URL", "http://127.0.0.1:%d/ai/request" % cls.stub.port),
        ]
        for p in cls.patches:
            p.start()
        cls.port = _free_port()
        cfg = AgentConfig(host="127.0.0.1", port=cls.port, force_mock=True, token="s3cret")
        cls.server = srv.AgentServer(cfg)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls._wait_connectable(cls.port)

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.stub.stop()
        for f in (cls.tokens_path, cls.audit_path, cls.audit_path + ".1"):
            try:
                os.unlink(f)
            except OSError:
                pass

    @staticmethod
    def _wait_connectable(port, tries=50):
        import socket
        for _ in range(tries):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("server did not come up")

    def _audit_lines(self):
        try:
            with open(self.audit_path, encoding="utf-8") as fh:
                return [json.loads(l) for l in fh.read().splitlines() if l]
        except OSError:
            return []

    def test_ai_token_ops_audited_dev_not(self):
        before = len(self._audit_lines())
        with GoseClient("127.0.0.1", self.port, token="ai-tok") as c:
            self.assertTrue(c.ping()["pong"])                      # allowed (observe)
            with self.assertRaises(GoseClientError) as ctx:
                c.call("games.launch", system="nes", game="x")     # denied (needs play)
            self.assertEqual(ctx.exception.code, "ERR_DENIED")
        with GoseClient("127.0.0.1", self.port, token="s3cret") as c:
            self.assertTrue(c.ping()["pong"])                      # dev — NOT audited
        time.sleep(0.2)
        new = self._audit_lines()[before:]
        self.assertEqual(len(new), 2)        # exactly the two TestAI ops, not the dev ping
        self.assertEqual([(r["name"], r["op"], r["ok"]) for r in new],
                         [("TestAI", "ping", True), ("TestAI", "games.launch", False)])
        self.assertEqual(new[1]["code"], "ERR_DENIED")

    def test_bad_token_mentions_pairing(self):
        with self.assertRaises(GoseClientError) as ctx:
            with GoseClient("127.0.0.1", self.port, token="wrong") as c:
                c.ping()
        self.assertIn("pair", str(ctx.exception).lower())

    def test_preauth_pair_request_forwards_without_granting(self):
        with GoseClient("127.0.0.1", self.port) as c:              # NO token
            r = c.call("pair.request", name="NewAI", tier="play")
            self.assertTrue(r["requested"])
            # ...and the connection still has zero access:
            with self.assertRaises(GoseClientError) as ctx:
                c.ping()
            self.assertEqual(ctx.exception.code, "ERR_AUTH")
        self.assertEqual(self.stub.requests[-1], {"name": "NewAI", "tier": "play"})
        # a request is not a grant: token map untouched
        self.assertNotIn("NewAI", str(srv._load_ai_tokens()))


if __name__ == "__main__":
    unittest.main()
