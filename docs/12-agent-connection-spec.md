# 12 — Agent connection spec (how AI agents connect)

**Status:** RESOLVED for v0 (2026-06-04). This is the spec the
`ai-bridge/` README and `mcp/README.md` were waiting on. It unblocks the item the
project CLAUDE.md listed under "Open items needing the owner's input → Confirm
the AI agents' MCP transport (stdio vs HTTP/SSE) + auth."

## The key realization
The question "how do the AI agents expose themselves?" was framed as blocked on a
decision the owner had to hand down. It isn't — it's answerable by looking at **how the
agents already connect to everything else.** The owner's agents are **Claude
Code sessions**. A CC session does not *expose* an API for a device to call; it is
an **MCP client** that *drives* tools registered in its `.mcp.json`. An agent's voice
and messaging tools already work exactly this way today — FastMCP/JSON-RPC
**stdio** servers listed in `<agent-home>\.mcp.json`. GOSE already ships the matching
piece: `mcp/gose_mcp_server.py`, a zero-dep stdio JSON-RPC MCP server.

So the agents don't need to expose anything. They **register the GOSE MCP server**
and call its tools. Decision made by introspecting the real architecture, not by
guessing a transport.

## v0 transport: MCP over stdio (the agent's machine) → TCP (to the device)

```
The agent's CC session       gose_mcp_server.py            GOSE Agent daemon
(MCP client, stdio)   ──▶    (subprocess on the agent's ──▶ (on the Odin 2, or a
                              machine, stdio JSON-RPC)      mock; owns the device)
                                     │  GoseClient
                                     └─ newline-delimited JSON over TCP  ──────┘
                                        (GOSE_HOST:GOSE_PORT, GOSE_TOKEN)
```

- **MCP layer = stdio, runs on the agent's machine.** The CC session spawns
  `gose_mcp_server.py` as a child process (same as its other stdio servers). No network for the
  MCP hop, no HTTP server to stand up, no MCP-level auth surface.
- **Device hop = the already-built GoseClient TCP** (ADR: newline-delimited JSON
  over asyncio TCP — `docs/05-ai-control-protocol.md`). This is the only hop that
  crosses the wire to the Odin 2, and it already exists, tested.
- **Auth = `GOSE_TOKEN`** on the TCP hop (already plumbed through `GoseClient` and
  the agent daemon; the daemon warns and restricts to loopback when no token set).
  Set a shared secret once the device is reachable over Wi-Fi/USB-net.

### Why not HTTP/SSE for v0
HTTP/SSE MCP transport only matters if the **MCP server itself** must run somewhere
other than the agent's machine — e.g. a cloud-hosted agent, or driving GOSE from a
phone off-LAN. For a home-LAN agent on the same box that spawns the server, stdio is
strictly simpler and has no auth/exposure surface. HTTP/SSE is a **planned v1
add-on** (both are wanted eventually, 2026-06-04) — the tool layer is identical, so
it's an additive transport, not a rework.

## Concrete: how an agent registers GOSE (live as of 2026-06-04)
Added to `<agent-home>\.mcp.json` (inert until the CC session relaunches — MCP servers
are spawned at session launch):
```json
"gose": {
  "type": "stdio",
  "command": "py",
  "args": ["-3.11", "D:\\GOSE-gaming-operating-system-emulator\\mcp\\gose_mcp_server.py"],
  "env": { "GOSE_HOST": "127.0.0.1", "GOSE_PORT": "8731" }
}
```
- `GOSE_HOST/PORT` = `127.0.0.1:8731` today (mock/local agent). **When the Odin 2
  arrives:** point `GOSE_HOST` at the device IP and add `GOSE_TOKEN` (matching the
  daemon's `GOSE_AGENT_TOKEN`). Nothing else changes.
- The MCP server connects to the agent **lazily** (only on `tools/call`), so an idle
  registration with no daemon running is harmless — `initialize`/`tools/list`
  never touch TCP.

## Verified end-to-end (2026-06-04, this machine, mock agent)
- `gose_mcp_server.py` launches under `py -3.11`, `tools/list` returns all 13 tools.
- With the mock agent (`GOSE_AGENT_FORCE_MOCK=1 py -3.11 -m gose_agent`) listening
  on `0.0.0.0:8731`, a full `tools/call` chain works:
  `gose_ping` → `{"pong": true}`, `gose_status` → live status object.
- **Pending:** the agent driving these as *registered MCP tools from inside its own
  session* — requires a relaunch (the `.mcp.json` add is inert until then).

## What this resolves in the repo
- `ai-bridge/` "🧱 blocked on the agent spec" → **unblocked.** For LLM tool-callers
  (AI agents/Claude), the integration is **pattern 1 (tool/function-calling)
  via the MCP server** — `ai-bridge/bridge.py`'s `AgentConnector` stub is only
  needed for the *non-MCP* "intent translation" path (pattern 2), which is now
  optional, not the primary route.
- CLAUDE.md open item "confirm MCP transport (stdio vs HTTP/SSE) + auth" →
  **stdio + GOSE_TOKEN for v0; HTTP/SSE is a v1 additive transport.**

## Open / next
- **v1:** add HTTP/SSE transport to `gose_mcp_server.py` for off-machine/off-LAN
  agents (cloud-hosted agent, phone). Additive; tools unchanged.
- Decide per-agent identity/token when more than one agent drives one device at once
  (today: one token, loopback-or-shared-secret).
