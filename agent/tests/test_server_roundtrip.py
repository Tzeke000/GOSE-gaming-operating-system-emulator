"""End-to-end: start the real asyncio server in a thread, drive it with the
stdlib client over a loopback socket. Proves transport + auth + dispatch + events.
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))

from gose_agent.config import AgentConfig  # noqa: E402
from gose_agent.server import AgentServer  # noqa: E402
from gose_client import GoseClient, GoseClientError  # noqa: E402


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestServerRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.cfg = AgentConfig(host="127.0.0.1", port=cls.port,
                              force_mock=True, token=None)  # loopback, no token = open
        cls.server = AgentServer(cls.cfg)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls._wait_connectable(cls.port)

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

    def test_ping_and_run(self):
        with GoseClient("127.0.0.1", self.port) as c:
            self.assertTrue(c.ping()["pong"])
            r = c.run("echo roundtrip")
            self.assertIn("roundtrip", r["stdout"])
            info = c.info()
            self.assertEqual(info["backends"]["input"], "mock")

    def test_auth_required_for_token_server(self):
        # A server WITH a token must reject a tokenless/incorrect client even on loopback.
        port = _free_port()
        cfg = AgentConfig(host="127.0.0.1", port=port, force_mock=True, token="s3cret")
        srv = AgentServer(cfg)
        threading.Thread(target=srv.run, daemon=True).start()
        self._wait_connectable(port)
        with GoseClient("127.0.0.1", port, token="wrong") as c:
            with self.assertRaises(GoseClientError) as ctx:
                c.ping()
            self.assertEqual(ctx.exception.code, "ERR_AUTH")
        with GoseClient("127.0.0.1", port, token="s3cret") as c:
            self.assertTrue(c.ping()["pong"])


if __name__ == "__main__":
    unittest.main()
