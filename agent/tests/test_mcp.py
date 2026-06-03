"""MCP server: drive the agent through the Model Context Protocol adapter."""
import os
import socket
import sys
import threading
import time
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "client"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "mcp"))

from gose_agent.config import AgentConfig  # noqa: E402
from gose_agent.server import AgentServer  # noqa: E402
from gose_mcp_server import GoseMCPServer  # noqa: E402


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class TestMCP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cfg = AgentConfig(host="127.0.0.1", port=cls.port, force_mock=True, token=None)
        threading.Thread(target=AgentServer(cfg).run, daemon=True).start()
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close(); break
            except OSError:
                time.sleep(0.05)
        cls.mcp = GoseMCPServer("127.0.0.1", cls.port)

    def test_initialize(self):
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "gose-agent")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_initialized_notification_no_response(self):
        self.assertIsNone(self.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tools_list(self):
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertIn("gose_run", names)
        self.assertIn("gose_state_read", names)
        for t in r["result"]["tools"]:
            self.assertIn("inputSchema", t)

    def test_tools_call_ping(self):
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "gose_ping", "arguments": {}}})
        self.assertNotIn("isError", r["result"])
        self.assertIn("pong", r["result"]["content"][0]["text"])

    def test_tools_call_run(self):
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "gose_run", "arguments": {"cmd": "echo mcp_ok"}}})
        self.assertIn("mcp_ok", r["result"]["content"][0]["text"])

    def test_unknown_tool_is_error(self):
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                             "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(r["result"].get("isError"))

    def test_unknown_method(self):
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 6, "method": "bogus/method"})
        self.assertEqual(r["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
