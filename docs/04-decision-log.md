# Decision Log (ADRs)

Append-only. Newest at top. Each: context → decision → status. Revisit freely;
mark superseded ones rather than deleting.

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
