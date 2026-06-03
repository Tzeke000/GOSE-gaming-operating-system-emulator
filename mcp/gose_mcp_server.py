#!/usr/bin/env python3
"""GOSE MCP server — drive the whole Odin 2 from any MCP client.

This is how Ava/Wren/Iris (and Claude) connect: a Model Context Protocol server
over stdio (newline-delimited JSON-RPC 2.0) that exposes the GOSE Agent's
capabilities as MCP tools. It is a thin adapter — it connects to the long-running
GOSE Agent daemon over localhost TCP (the daemon owns the real device: uinput,
the running game, etc.), so the same device can be driven over MCP, the raw
JSON-lines protocol, or SSH/console interchangeably.

Zero external dependencies (mirrors the agent's design) so it runs on the device
and is testable anywhere. Inspired by the existing `mcp-retroarch` project, but
GOSE controls the *whole device* (input, shell/OS, games, screen, game-state),
not just RetroArch.

Run (an MCP client launches this):
    GOSE_TOKEN=... python3 mcp/gose_mcp_server.py
Config via env: GOSE_HOST (127.0.0.1), GOSE_PORT (8731), GOSE_TOKEN.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent", "client"))
from gose_client import GoseClient, GoseClientError  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "gose-agent", "version": "0.1.0"}

# Each tool maps an MCP tool name -> a GOSE Agent op + JSON-Schema for arguments.
TOOLS = [
    {"name": "gose_ping", "op": "ping",
     "description": "Health check the device agent.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "gose_status", "op": "system.status",
     "description": "Device health: battery, temperature, memory, Wi-Fi, uptime.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "gose_run", "op": "system.run",
     "description": "Run a shell command on the device (OS repair/tinkering).",
     "schema": {"type": "object", "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"]}},
    {"name": "gose_tap", "op": "input.button",
     "description": "Tap a controller button (a,b,x,y,up,down,left,right,l1,r1,start,...).",
     "schema": {"type": "object", "properties": {
         "button": {"type": "string"}, "action": {"type": "string", "default": "tap"}},
         "required": ["button"]}},
    {"name": "gose_axis", "op": "input.axis",
     "description": "Set an analog axis (lx,ly,rx,ry,lt,rt) to -1.0..1.0.",
     "schema": {"type": "object", "properties": {
         "axis": {"type": "string"}, "value": {"type": "number"}},
         "required": ["axis", "value"]}},
    {"name": "gose_systems", "op": "games.systems",
     "description": "List installed emulation systems.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "gose_list_games", "op": "games.list",
     "description": "List games for a system.",
     "schema": {"type": "object", "properties": {"system": {"type": "string"}},
                "required": ["system"]}},
    {"name": "gose_launch", "op": "games.launch",
     "description": "Launch a game (by name or path) on a system.",
     "schema": {"type": "object", "properties": {
         "system": {"type": "string"}, "game": {"type": "string"}},
         "required": ["system", "game"]}},
    {"name": "gose_stop", "op": "games.stop",
     "description": "Stop the running game.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "gose_screenshot", "op": "screen.capture",
     "description": "Capture the screen as base64 PNG (vision fallback).",
     "schema": {"type": "object", "properties": {}}},
    {"name": "gose_state_profiles", "op": "state.profiles",
     "description": "List available game-state RAM-map profiles.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "gose_state_attach", "op": "state.attach",
     "description": "Attach a game-state profile (auto-detects if omitted).",
     "schema": {"type": "object", "properties": {"profile": {"type": "string"}}}},
    {"name": "gose_state_read", "op": "state.read",
     "description": "Read structured game state from emulator memory (no screenshot).",
     "schema": {"type": "object", "properties": {"profile": {"type": "string"}}}},
]
_BY_NAME = {t["name"]: t for t in TOOLS}


class GoseMCPServer:
    def __init__(self, host="127.0.0.1", port=8731, token=None):
        self.client = GoseClient(host, port, token=token)

    def handle(self, msg: dict):
        """Return a JSON-RPC response dict, or None for notifications."""
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL_VERSION,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": SERVER_INFO}
            elif method in ("notifications/initialized", "initialized"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": t["name"], "description": t["description"],
                                     "inputSchema": t["schema"]} for t in TOOLS]}
            elif method == "tools/call":
                result = self._call_tool(params.get("name"), params.get("arguments") or {})
            else:
                return self._err(mid, -32601, f"method not found: {method}")
        except Exception as e:  # noqa: BLE001
            return self._err(mid, -32603, f"internal error: {e}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call_tool(self, name, arguments) -> dict:
        tool = _BY_NAME.get(name)
        if not tool:
            return {"content": [{"type": "text", "text": f"unknown tool: {name}"}],
                    "isError": True}
        try:
            res = self.client.call(tool["op"], **arguments)
            return {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]}
        except GoseClientError as e:
            return {"content": [{"type": "text", "text": f"{e.code}: {e.message}"}],
                    "isError": True}

    @staticmethod
    def _err(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    def serve(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self.handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()


def main():
    srv = GoseMCPServer(
        host=os.environ.get("GOSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("GOSE_PORT", "8731")),
        token=os.environ.get("GOSE_TOKEN"),
    )
    srv.serve()


if __name__ == "__main__":
    main()
