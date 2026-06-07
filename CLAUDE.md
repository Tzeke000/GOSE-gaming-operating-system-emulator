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
agents** over Wi-Fi or cable.

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
- **Base distro: ROCKNIX only + Android** (owner, 2026-06-03, ADR-0012 — supersedes
  the earlier "both in parallel" plan). One Linux slot, pick the best → **ROCKNIX**
  (verified-stable, officially supported on Odin 2). Dual-boot = ROCKNIX (microSD) +
  stock Android (internal). **Batocera = documented fallback** only. Custom code
  stays **distro-agnostic** so a swap stays cheap.
- **Device not yet acquired** (owner, 2026-06-03) — keep everything
  **variant-agnostic** (Odin 2 / Mini / Portal all viable). No hardware-specific
  assumptions until the unit is in hand.
- **GOSE on PC = a virtual machine** (owner, 2026-06-03, ADR-0013) — a separate
  **x86_64 GOSE image** (base Batocera x86_64 + GOSE layer) booted in **QEMU**, not
  a web wrapper or ARM emulation. The owner uses the PC app first until the Odin 2
  arrives. Launcher `scripts/gose_vm.py`; UI-only preview `scripts/gose-preview.py`.
  Image build = next milestone `[needs build]`.
- **Boot-time input chooser** (ADR-0013): device default **Native** (auto-accepts
  peripherals), PC default **Keyboard** (can pick Controller). `scripts/gose_input.py`.
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
- `agent/client/` — Python client SDK + CLI for the AI side (the AI agents) and for testing.
- `agent/gose_agent/profiles/` — per-game RAM maps for the **game-state interface**
  ("Mineflayer for retro"): read game state from emulator memory, no screenshots.
  Accepts stable-retro type descriptors; `agent/tools/import_stable_retro.py` imports
  their maps. See `docs/08-game-state-interface.md`. Demo: `agent/examples/pong_no_screenshots.py`.
- `gui/mockup/` — concept PNGs + **navigable HTML prototypes**: boot splash
  (`boot.html`), login/user-select (`login.html`), the **GOSE Boot Menu / "BIOS"**
  (`bootmenu.html`), and the Windows-like desktop (`desktop.html`). Shared theme
  tokens in `assets/themes.css` (default sleek-black **onyx**, switchable in
  Settings). Multi-input: gamepad focus-nav + pointer + mouse + keyboard + PS5.
  See `docs/06-gui-plan.md`; boot/BIOS model in `docs/10-boot-menu.md`.
- `gui/` — Windows-like front-end work (theme and/or custom app). `[CUSTOM]`
- `mcp/` — **MCP server**: how AI agents/Claude drive the device (stdio
  JSON-RPC, proxies to the agent). Zero-dep. See `mcp/README.md`.
- `docs/09-toolchain.md` — curated open-source tools to adopt (coding→OS→games→design).
- `scripts/` — reproducible, idempotent device setup scripts + mock-testable logic:
  `gose_bootmenu.py` (Boot Menu trigger), `gose_input.py` (platform/input model),
  `gose_vm.py` (**GOSE-PC VM launcher**, QEMU command builder), `gose-preview.py`
  (zero-dep UI preview in a browser).
- `pc-image/` — **GOSE-PC image build**: `build-gose-pc.sh` (Batocera x86_64 + the
  `gose-layer/` → `.img`/`.ova`; dry-run works), `make_ova.py` (OVA packager,
  tested). Real build `[needs build]`. See `pc-image/README.md`.
- `ROADMAP.md` — build order + live status checklist.

## How to work in this repo
- Dev branch: **`main`** (the historical `claude/odin2-gaming-os-4SWOh` branch is
  retired, 61+ commits behind). Develop, commit, push to main. Do NOT open a PR
  unless the owner asks.
- Run the agent test suite (no deps, works in-container):
  `python3 -m unittest discover -s agent/tests -v`
- Run the agent in mock mode: `python3 -m gose_agent` (from `agent/`), then drive
  it with `python3 client/cli.py ping` (from `agent/`).
- Most "real device" steps (flashing, uinput, HDMI) can only be validated on the
  actual Odin 2 — mark those as **[needs hardware]** and keep them in the runbook.

## How the AIs connect (owner, 2026-06-03)
- **The AI agents will most likely use MCP**, or **SSH / console**. → We built an
  MCP server (`mcp/`) + the CLI works over SSH + `system.run` is the console path.
  Remaining: confirm any auth/transport specifics they need (e.g., HTTP/SSE MCP
  transport vs stdio). The tool layer is done either way.

## Open items needing the owner's input
- Confirm the AI agents' MCP transport (stdio vs HTTP/SSE) + auth, if any.
- Confirm exact Odin 2 variant once acquired (currently NOT yet purchased) —
  affects image + RAM headroom. Stay variant-agnostic until then.
- Does the Odin 2 support simultaneous OTG + charging? (affects portable multi-dongle).
