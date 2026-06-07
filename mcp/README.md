# GOSE MCP server

How **your AI agents** (and Claude) drive the Odin 2: a **Model Context
Protocol** server over stdio that exposes the GOSE Agent's capabilities as MCP
tools. Zero dependencies; mirrors the agent's design so it runs on the device.

```
MCP client (AI agent / Claude)    ──stdio JSON-RPC──▶  gose_mcp_server.py
                                                          │  GoseClient (TCP)
                                                          ▼
                                                   GOSE Agent daemon ──▶ Odin 2
```

The MCP server is a **thin adapter**: it connects to the long-running GOSE Agent
daemon over localhost TCP (the daemon owns the real device). So the same device is
drivable three ways, interchangeably:
1. **MCP** (this) — the standard for tool-calling agents.
2. **Raw JSON-lines protocol** — `agent/client` SDK/CLI.
3. **SSH / console** — `system.run` is the shell path; the CLI works over SSH.

## Tools exposed
`gose_ping`, `gose_status`, `gose_run`, `gose_tap`, `gose_axis`, `gose_systems`,
`gose_list_games`, `gose_launch`, `gose_stop`, `gose_screenshot`,
`gose_state_profiles`, `gose_state_attach`, `gose_state_read`.
(`tools/list` returns full JSON-Schemas.)

## Wire it into an MCP client
The agent daemon must be running (on the device or reachable). Example client
config (Claude Desktop / any MCP client):
```json
{
  "mcpServers": {
    "gose": {
      "command": "python3",
      "args": ["/path/to/GOSE/mcp/gose_mcp_server.py"],
      "env": { "GOSE_HOST": "192.168.1.50", "GOSE_PORT": "8731", "GOSE_TOKEN": "..." }
    }
  }
}
```

## Quick manual check
```bash
# start a mock agent (from agent/):  GOSE_AGENT_FORCE_MOCK=1 python3 -m gose_agent &
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 mcp/gose_mcp_server.py
```

## Status / next
- ✅ Implemented + tested (`agent/tests/test_mcp.py`).
- ⬜ Once the AI agents' exact MCP expectations are known, add any auth/transport
  they require (e.g., HTTP/SSE transport instead of stdio) — the tool layer stays
  the same.
