# CLAUDE.md — GOSE project memory (read me first, every session)

> This file is auto-loaded at the start of every Claude Code session. It is the
> project's **persistent memory**: the cloud container is ephemeral and wiped
> between sessions, so anything not written here (or in `docs/`) and committed is
> lost. Keep this current. When you make a meaningful decision, log it in
> `docs/04-decision-log.md` and update the relevant section here.

## What this project is
**GOSE = Gaming Operating System Emulator.** Turn an **AYN Odin 2** (Snapdragon 8
Gen 2) into a console-like, controller-driven gaming + tinkering device running a
**flashable Linux OS**, with a Windows-style controller-only GUI, broad emulation,
universal controller support, and the ability to be **driven by the owner's AI
agents (Ava, Wren, Iris)** over Wi-Fi or cable.

Owner: **Zeke (Tzeke000)** · tzeke000@gmail.com
Full original brief: `docs/00-project-brief.md`

**This is NOT writing an OS from scratch.** It is: flash a mature handheld Linux
distro (ROCKNIX or Batocera) to SD, then *configure + extend* it. Only a few
pieces are genuinely custom (flagged `[CUSTOM]`): the Windows-like front-end, the
AI control agent, and the reproducible setup scripts.

## Verified facts (as of 2026-06, see docs/01-research-findings.md for sources)
- **ROCKNIX**: officially stable on all three Odin 2 variants (2, Mini, Portal).
  Boots from microSD, leaves Android intact. **Recommended base for "works now."**
- **Batocera v42**: supports Odin 2 via the **SM8550** image; biggest emulation
  coverage. Good second option / can dual-experiment on a 2nd SD card.
- Both need a **one-time bootloader (abl) modification** + a fastboot
  **"switch boot mode"** to boot Linux off SD. Android stays on internal storage.
- Front-ends: **Batocera uses `batocera-emulationstation`** (its OWN fork; XML
  theme format **v7**) — NOT ES-DE. ROCKNIX also uses an EmulationStation fork.
  (The original brief said "EmulationStation-DE" — that was inaccurate.)
- GPU accel = Freedreno/Turnip (Vulkan). Wi-Fi + Bluetooth work. Xbox pads via
  `xpadneo`; PS4/PS5 native; Switch pads fussiest.
- Known gotcha to design around: **dock HDMI on Linux can fail** (driver gaps) —
  keep a direct USB-C→HDMI adapter as fallback. Dock USB/Ethernet more reliable.

## Current decision (revisit anytime)
- **Base distro: ROCKNIX first** (stable today), Batocera on a spare SD for
  comparison. The custom code we write is **distro-agnostic** where possible.
- **AI control agent language: Python** (best `evdev`/`uinput` support, readable).
- **Control transport v0: newline-delimited JSON over asyncio TCP** (zero external
  deps, identical over Wi-Fi and USB-net, fully testable). Upgrade to WebSocket/TLS
  later. See `docs/05-ai-control-protocol.md`.

## Repo map
- `docs/` — brief, research, install runbook, architecture, decisions, protocol, GUI/controller plans.
- `agent/` — **GOSE Agent**: the device-side daemon the AI talks to. Runs on the
  Odin under ROCKNIX/Batocera. Capabilities: input injection, shell, game launch,
  system status, screen capture. Has **mock backends** so it runs/tests in any
  Linux container (no real `/dev/uinput` needed).
- `agent/client/` — Python client SDK + CLI for the AI side (Ava/Wren/Iris) and for testing.
- `gui/` — Windows-like front-end work (theme and/or custom app). `[CUSTOM]`
- `scripts/` — reproducible, idempotent device setup scripts.
- `ROADMAP.md` — build order + live status checklist.

## How to work in this repo
- Dev branch: **`claude/odin2-gaming-os-4SWOh`**. Develop, commit, push there. Do
  NOT open a PR unless Zeke asks.
- Run the agent test suite (no deps, works in-container):
  `python3 -m unittest discover -s agent/tests -v`
- Run the agent in mock mode: `python3 -m gose_agent` (from `agent/`), then drive
  it with `python3 client/cli.py ping` (from `agent/`).
- Most "real device" steps (flashing, uinput, HDMI) can only be validated on the
  actual Odin 2 — mark those as **[needs hardware]** and keep them in the runbook.

## Open items needing Zeke's input
- **AI agent spec**: how do Ava/Wren/Iris expose themselves? (endpoints, auth,
  message format). The agent currently defines OUR protocol; the bridge that maps
  Ava/Wren/Iris ↔ GOSE Agent needs their real API. Tracked in `docs/03-architecture.md`.
- Confirm exact Odin 2 variant (2 / Mini / Portal) — affects image + RAM headroom.
- Does the Odin 2 support simultaneous OTG + charging? (affects portable multi-dongle).
