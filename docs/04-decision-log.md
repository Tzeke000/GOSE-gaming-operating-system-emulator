# Decision Log (ADRs)

Append-only. Newest at top. Each: context → decision → status. Revisit freely;
mark superseded ones rather than deleting.

## ADR-0009 — AI connects via MCP (primary), with SSH/console as alternates
**Context:** Zeke confirmed (2026-06-03) Ava/Wren/Iris "most likely will use MCP,
or maybe SSH/console in." **Decision:** build a **zero-dependency MCP stdio server**
(`mcp/gose_mcp_server.py`) that proxies to the GOSE Agent daemon, exposing its
capabilities as MCP tools. Keep SSH (CLI + `system.run`) as first-class alternates.
**Alternatives:** official MCP Python SDK (heavier dep, harder to deploy on the
device) — kept as [ref]; we hand-roll the small tools-only stdio subset like we did
for the JSON-lines protocol. **Status:** accepted, implemented + tested. Transport
specifics (stdio vs HTTP/SSE) to confirm with Zeke; tool layer is transport-agnostic.

## ADR-0008 — Multi-input: focus-nav + gamepad-pointer + mouse/keyboard + PS5
**Context:** the Windows-style desktop must be driven by the native Odin pad, a
mouse+keyboard, and a PS5 DualSense. Desktops expect a pointer. **Decision:** two
complementary controller paths — (1) built-in **focus-nav** (highlight + A/B) as
default, (2) **pointer mode** via **AntiMicroX** with per-app auto-profiles for apps
that need a mouse. All inputs ride standard `evdev`; PS5 via native `hid-playstation`.
The HTML prototype implements both (Y toggles). **Status:** accepted; AntiMicroX
profile is `[on device]`.

## ADR-0007 — Game-state interface via RetroArch memory (no screenshots)
**Context:** Zeke wants the AI to play/observe games from **game state, not pixels**
(Mineflayer-style), at least for simple games (Pong, chess, Mario 64).
**Decision:** add a `state` capability that uses RetroArch's Network Command
Interface (UDP 55355) to read/write core memory, decoded through per-game RAM-map
**profiles** into named fields. Complements (doesn't replace) `screen.capture`.
**Caveats accepted:** core support varies (Mupen64Plus-Next, Mesen confirmed);
addresses are game/core specific and need on-hardware verification. **Status:**
accepted, implemented + tested against a mock RetroArch; real-address verification
is `[needs hardware]`.

## ADR-0006 — Adopt the existing ecosystem instead of reinventing
**Context:** Zeke: "research and see if there's anything like this already and just
import that if you can." There is. **Decision:** build on prior art —
- **stable-retro** (gym-retro fork): reuse its hundreds of `data.json` RAM maps;
  our profile engine accepts its `>u4`-style type descriptors and we ship a
  converter (`tools/import_stable_retro.py`).
- **pyraco** (PyPI): reference/optional transport for the RetroArch NCI (our
  built-in client stays zero-dep for guaranteed on-device operation).
- **mcp-retroarch**: an existing MCP server for RetroArch — validates the design
  and points to MCP as the likely **Ava/Wren/Iris** connection path; plan to expose
  the GOSE Agent over MCP (it controls the whole device, a superset).
**Status:** accepted. stable-retro type compat + converter implemented; pyraco/MCP
adoption tracked in ROADMAP.

## ADR-0005 — Mock backends so the agent is testable off-device
**Context:** Most of this project can only be fully validated on the Odin 2, which
slows iteration. **Decision:** every GOSE Agent capability has a real backend AND a
mock backend, auto-selected by probing the environment (`/dev/uinput` writable?
`evdev` importable? framebuffer present?). In the cloud container we get mocks, so
logic/protocol/tests run green anywhere. **Status:** accepted, implemented.

## ADR-0004 — Control transport v0 = JSON-lines over asyncio TCP
**Context:** Need an AI↔device control channel over both Wi-Fi and USB, easy to
test, no heavy deps. **Decision:** newline-delimited JSON messages over a stdlib
`asyncio` TCP server; token auth; same protocol on Wi-Fi/Ethernet and USB-net.
**Alternatives:** WebSocket (needs a lib, nicer for browsers/streaming), gRPC
(heavy, codegen). **Decision:** start with TCP/JSON-lines (zero deps, trivially
testable); upgrade to WebSocket+TLS once the shape stabilizes. **Status:** accepted.

## ADR-0003 — AI control agent in Python
**Context:** The agent needs robust input injection (`uinput`), shell, and quick
iteration. **Decision:** Python — best `evdev`/`uinput` bindings, readable for
Zeke, ships on both distros. **Alternatives:** Go/Rust (single static binary, nicer
to deploy) considered for later if startup time/footprint matters. **Status:**
accepted for v0.

## ADR-0002 — Custom code is distro-agnostic where possible
**Context:** We may run ROCKNIX now and Batocera later (or switch). **Decision:**
the GOSE Agent, client SDK, and protocol depend only on generic Linux (uinput,
shell, framebuffer, `gamelist.xml` convention) — not on ROCKNIX/Batocera
internals. Distro-specific bits (theme format, paths) are isolated in config +
`scripts/setup-device.sh`. **Status:** accepted.

## ADR-0001 — Base distro: run BOTH in parallel, then bench
**Context:** As of 2026-06 both support the Odin 2; ROCKNIX is officially stable on
all three variants, Batocera v42 (SM8550) has the bigger library but is newer to
the device. **Decision (Zeke, 2026-06-03):** stand up **ROCKNIX and Batocera on
two SD cards in parallel** and benchmark PSP/PS2/Switch on each, then pick the
daily driver from real results. Device is **not yet acquired**, so all work stays
**variant-agnostic** (Odin 2 / Mini / Portal). **Status:** accepted, pending
hardware purchase + test.
