# GOSE Architecture

```
                     ┌─────────────────────────────────────────────┐
   Zeke's server /   │                AYN Odin 2 (Linux: ROCKNIX/Batocera)
   AI agents         │                                             │
 ┌───────────┐       │   ┌───────────────┐   ┌──────────────────┐  │
 │ Ava / Wren│       │   │  Front-end    │   │  Emulators       │  │
 │ / Iris    │       │   │  (Windows-like│   │  PSP, PS2, ...   │  │
 │ (LLM host)│       │   │   ES theme or │   │  RetroArch cores │  │
 └─────┬─────┘       │   │   custom app) │   └────────▲─────────┘  │
       │             │   └───────▲───────┘            │ launch     │
       │ AI-bridge   │           │ navigate           │            │
       │ (maps their │   ┌───────┴────────────────────┴─────────┐  │
       │  API ↔ ours)│   │            GOSE AGENT (daemon)        │  │
       ▼             │   │  capabilities:                        │  │
 ┌───────────┐  Wi-Fi/   │   input  · system · games · screen    │  │
 │ gose      │  Ethernet │  transport: JSON-lines over TCP       │  │
 │ client SDK│◀────────▶ │  (Wi-Fi or USB-net), token auth       │  │
 └───────────┘   or USB  └───────────────────────────────────────┘  │
                 cable   │   uinput (virtual pad/kbd) · shell ·  fb  │
                         └─────────────────────────────────────────┘
```

## Components

### 1. Base OS (configure, don't build) — ROCKNIX / Batocera
Provides kernel + drivers + emulators + a front-end. We layer config + custom
pieces on top via `scripts/setup-device.sh`.

### 2. Front-end (Windows-like, controller-only) `[CUSTOM]`
Either a custom theme for the distro's EmulationStation, or a standalone custom
app. Decision + plan in `06-gui-plan.md`. Launches emulators; exposes a "Tools"
area (terminal, file manager, network tools, AI bridge launcher).

### 3. GOSE Agent (device-side daemon) `[CUSTOM]` — `agent/`
The thing the AI controls the device *through*. Single daemon, runs on the Odin.
Capabilities:
- **input** — inject controller + keyboard events via `uinput` (a virtual gamepad
  the emulators see as a real pad). Lets the AI literally "play."
- **system** — run shell commands, report status (battery/temp/mem/wifi),
  start/stop services. This is the "help fix the OS" path.
- **games** — enumerate systems + titles (parse `gamelist.xml`), launch/kill the
  right emulator.
- **screen** — capture the framebuffer so the AI can "see" the screen.
- (later) **voice** — USB mic in / TTS out.
Transport = newline-delimited JSON over asyncio TCP, token-authenticated. Same
protocol whether the AI reaches it over **Wi-Fi/Ethernet** or a **USB cable**
(USB gadget networking brings up a `usb0` interface; agent just listens on it).
Has **mock backends** so it runs and is testable on any Linux (incl. this
container) without real `/dev/uinput`. Protocol spec: `05-ai-control-protocol.md`.

### 4. gose client SDK + CLI — `agent/client/`
What the AI side imports/calls. `gose_client.GoseClient` gives high-level methods
(`press`, `tap`, `run`, `launch`, `status`, `screenshot`). `cli.py` is for humans
to test the agent without an LLM.

### 5. AI bridge (maps Ava/Wren/Iris ↔ GOSE) `[CUSTOM]` — `ai-bridge/` (stub)
**Blocked on Zeke** providing the Ava/Wren/Iris API (endpoints, auth, message
format). Once known, the bridge translates their intents ("open PSP, launch
God of War, press X") into `gose client` calls, and streams status/screens back.
Until then, the GOSE Agent + client SDK fully define and exercise OUR side, so the
device is controllable today via the CLI/SDK and the bridge is a thin adapter.

## Two ways "the AI controls the Odin"
1. **Play games** — `input.*` injects pad events into the running emulator;
   `games.launch` starts titles; `screen.capture` closes the perception loop.
2. **Fix the OS** — `system.run` executes shell (update configs, restart services,
   pair controllers, read logs); `system.status` reports health. This is the
   "remote hands" / tinkering path the brief asks for.

## Transport paths (wireless AND cable)
- **Wireless:** Wi-Fi or dock Ethernet → agent listens on `0.0.0.0:<port>`.
- **Cable:** USB-C in **USB gadget mode** → expose a USB network device (RNDIS/ECM
  via configfs) so the tower gets a `usb0` link to the Odin; agent listens there
  too. Fallback: USB serial gadget speaking the same JSON-lines protocol.
  (Gadget setup is `[needs hardware]`; documented in the agent README.)

## Security model
- Shared **token** required for any non-loopback connection (`GOSE_AGENT_TOKEN`).
- `system.run` (arbitrary shell) is powerful by design (OS repair) — gated by a
  config flag `allow_shell` (default on for the owner's device) and the token.
- Bind to LAN/USB by default; document an SSH tunnel for remote-over-internet
  instead of exposing the port publicly. Add TLS when we move to WebSocket.
