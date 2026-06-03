# GOSE Agent Control Protocol (v0)

Transport: **newline-delimited JSON** (one JSON object per line, UTF-8) over a TCP
socket. Identical over Wi-Fi/Ethernet and USB-net. This is the contract between the
**gose client** (AI side) and the **GOSE Agent** (device side).

## Framing
- Each message = one compact JSON object + `\n`.
- Client → agent = **requests**. Agent → client = **responses** and async **events**.

## Request
```json
{ "id": "uuid-or-counter", "op": "namespace.action", "args": { }, "token": "secret" }
```
- `id` — echoed back so the client can match responses. Required.
- `op` — `namespace.action` (see ops below). Required.
- `args` — op-specific object. Optional.
- `token` — required for non-loopback connections; ignored on loopback if the agent
  was started without a token.

## Response
```json
{ "id": "same-id", "ok": true,  "result": { } }
{ "id": "same-id", "ok": false, "error": "message", "code": "ERR_CODE" }
```

## Event (unsolicited, agent → client)
```json
{ "event": "namespace.kind", "data": { } }
```
e.g. `game.launched`, `game.exited`, `system.warning`.

## Auth handshake
First message on a non-loopback socket must carry a valid `token`, or the agent
replies `ok:false, code:"ERR_AUTH"` and closes. On loopback with no token
configured, auth is skipped (dev convenience).

## Operations (v0)

### Meta
| op | args | result |
|----|------|--------|
| `ping` | — | `{ "pong": true, "ts": <epoch> }` |
| `agent.info` | — | `{ "version", "host", "backends": {...}, "ops": [...] }` |

### input  (the AI "plays")
Buttons use a standard pad vocabulary: `a b x y up down left right l1 r1 l2 r2 l3
r3 start select guide`.
| op | args | result |
|----|------|--------|
| `input.button` | `{ "button", "action": "press|release|tap", "duration_ms?": 80 }` | `{ "done": true }` |
| `input.combo` | `{ "buttons": ["l1","r1"], "duration_ms?": 80 }` | `{ "done": true }` |
| `input.axis` | `{ "axis": "lx|ly|rx|ry|lt|rt", "value": -1.0..1.0 }` | `{ "done": true }` |
| `input.type` | `{ "text": "hello" }` (virtual keyboard) | `{ "done": true }` |

### system  (the AI "fixes the OS" / tinkers)
| op | args | result |
|----|------|--------|
| `system.run` | `{ "cmd": "uname -a", "timeout_ms?": 10000 }` | `{ "code", "stdout", "stderr" }` |
| `system.status` | — | `{ "battery", "temp_c", "mem", "cpu", "wifi", "uptime" }` |
| `system.service` | `{ "name", "action": "start|stop|restart|status" }` | `{ "code", "stdout" }` |

### games
| op | args | result |
|----|------|--------|
| `games.systems` | — | `{ "systems": ["psp","ps2",...] }` |
| `games.list` | `{ "system": "psp" }` | `{ "games": [ { "name", "path" }, ... ] }` |
| `games.launch` | `{ "system", "game" }` (name or path) | `{ "pid" }` + event `game.launched` |
| `games.stop` | — | `{ "stopped": true }` + event `game.exited` |

### screen  (the AI "sees")
| op | args | result |
|----|------|--------|
| `screen.capture` | `{ "format?": "png", "scale?": 0.5 }` | `{ "format", "w", "h", "b64" }` |

### state  (the AI reads game state from memory — no screenshots)
Talks to RetroArch's Network Command Interface. See `08-game-state-interface.md`.
| op | args | result |
|----|------|--------|
| `state.profiles` | — | `{ "profiles": {...}, "active" }` |
| `state.attach` | `{ "profile?": "mario64" }` (auto-detect if omitted) | `{ "attached", "detected" }` |
| `state.read` | `{ "profile?": }` | `{ "profile", "fields": { "mario_x": 1.5, ... }, "ts" }` |
| `state.status` | — | `{ "state", "core", "game", "crc" }` |
| `state.read_raw` | `{ "address", "count", "method?": "core_memory|core_ram" }` | `{ "address", "bytes", "hex" }` |
| `state.write_raw` | `{ "address", "data", "method?" }` | `{ "address", "written" }` |

## Versioning
`agent.info.version` carries the agent version; `ops` lists supported ops so a
client can feature-detect. Breaking changes bump the protocol and this doc.

## Error codes
`ERR_AUTH`, `ERR_BADREQ`, `ERR_UNKNOWN_OP`, `ERR_ARGS`, `ERR_BACKEND`,
`ERR_TIMEOUT`, `ERR_DENIED` (e.g. shell disabled).
