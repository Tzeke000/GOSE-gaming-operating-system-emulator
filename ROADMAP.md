# GOSE Roadmap & Live Status

Legend: ✅ done · 🟡 in progress · ⬜ todo · 🔌 `[needs hardware]` (can't finish in
the cloud container) · 🧱 blocked

## Phase 0 — Foundation (this repo)
- ✅ Verify Odin 2 Linux support (ROCKNIX stable; Batocera v42 SM8550)
- ✅ Project memory + docs (`CLAUDE.md`, `docs/`)
- ✅ Architecture + control protocol defined
- ✅ GOSE Agent scaffold w/ mock backends + tests (runs in-container)
- ✅ Client SDK + CLI
- ✅ Reproducible setup scripts (skeleton)
- ✅ SessionStart hook so web sessions are primed

## Phase 1 — Base OS on hardware 🔌  (device NOT yet acquired)
- ⬜ Acquire Odin 2 (+ Super Dock, A2 microSD ×2, 8BitDo Adapter 2, USB-C→HDMI)
- ⬜ Confirm exact variant on arrival (2 / Mini / Portal)
- ⬜ **Single Linux = ROCKNIX** (ADR-0012): flash ROCKNIX to microSD; abl mod; boot
      from SD alongside stock Android (runbook §D). Batocera = fallback only.
- ⬜ First-boot checklist (controller nav, Wi-Fi, BT, GPU, audio) (§F)
- ⬜ Bench PSP/PS2/Switch on ROCKNIX → confirm it's the keeper

## Phase 2 — Emulation 🔌
- ⬜ PSP runs great, upscaled (flagship)
- ⬜ Standard ladder w/ sane defaults (NES…PS2, GC, N64, Dreamcast, arcade)
- ⬜ Switch best-effort per-title; light PC via Box64/Wine

## Phase 3 — Controllers 🔌
- ⬜ Pair/verify Xbox (xpadneo), PS4/5, Switch, 8BitDo
- ⬜ 8BitDo USB Adapter 2 universal path; multi-controller; 6 via dock

## Phase 4 — Peripherals 🔌
- ⬜ HDMI (dock + USB-C→HDMI fallback), USB hub, Ethernet, USB mic
- ⬜ Confirm simultaneous OTG + charging

## Phase 5 — Windows-like GUI `[CUSTOM]`
- ✅ Windows-PC concept images + **navigable HTML prototypes** (`gui/mockup/`)
- ✅ Boot splash + login/user-select screens (boot.html, login.html)
- ✅ GOSE Boot Menu / "BIOS" — hold L1+R1 at power-on; mock-tested trigger logic
  (`scripts/gose_bootmenu.py`) + `bootmenu.html`. Real evdev/GPIO read [needs hardware]
- ✅ Theme system: default Onyx (sleek black) + Midnight/Neon/Light, switchable in Settings
- ✅ Vendored Lucide icons + Inter font (licenses incl.)
- ✅ Multi-input in prototype: gamepad focus-nav + gamepad pointer + mouse/kbd + PS5
- ✅ Boot-time navigation chooser (`input-select.html`) + platform/input model
  (`scripts/gose_input.py`, `assets/platform.js`): device→Native, PC→Keyboard
- ✅ GOSE on PC = **VM** (ADR-0013): QEMU launcher `scripts/gose_vm.py` (tested) +
  `scripts/gose-preview.py` UI preview
- ✅ GOSE-PC image build scaffolded (`pc-image/`): GOSE layer + OVA packager
  (tested) + `build-gose-pc.sh` (dry-run verified)
- ✅ New GOSE logo wired into boot splash, boot menu, login, desktop Start button
- ⬜ **Run the real GOSE-PC build** (pin Batocera x86_64; on a Linux host) → publish
  `GOSE-PC.img` + `GOSE-PC.ova` `[needs build]`
- ⬜ Windows-like EmulationStation theme (`pc-image/gose-layer/themes/gose/`) `[next]`
- ✅ Toolchain curated (`docs/09-toolchain.md`); input stack (`docs/07-controllers.md`)
- ⬜ Ship AntiMicroX profile for desktop pointer; Skyscraper for box art `[on device]`
- 🟡 Plan written (`docs/06-gui-plan.md`)
- ⬜ Lock the visual direction with Zeke, then pick Path A vs B
- ⬜ Path A: Windows-style ES theme (home→system→library→launch, controller-only)
- ⬜ Tools area (terminal, file manager, network tools, AI bridge launcher)
- ⬜ Evaluate Path B (custom front-end) where theme falls short

## Phase 6 — AI control `[CUSTOM]`
- ✅ GOSE Agent: input/system/games/screen capabilities (mock-backed)
- ✅ **Game-state interface** (read state from memory, no screenshots) + profiles
- ✅ Adopt ecosystem: stable-retro type compat + importer (ADR-0006)
- 🔌 Real backends on device (uinput pad, framebuffer capture, real game launch)
- 🔌 Verify RAM-map addresses on hardware (Mario 64 via Mupen64Plus-Next)
- 🔌 USB-cable path (USB gadget networking → `usb0`)
- ✅ Expose GOSE Agent over **MCP** (`mcp/` — Ava/Wren/Iris/Claude connect via standard)
- ✅ SSH/console path (CLI over SSH + `system.run`)
- ⬜ Confirm MCP transport/auth specifics with Zeke (stdio vs HTTP/SSE)
- 🟡 AI bridge: MCP is the main path now; bridge.py kept for non-MCP/intent style

## Phase 7 — Reproducibility
- 🟡 `scripts/setup-device.sh` grows with every customization
- ⬜ Full "re-flash → one script → restored" validated on hardware

## Immediate next actions
1. Zeke: confirm Odin 2 variant + whether to start with ROCKNIX (recommended).
2. Zeke: share the Ava/Wren/Iris API (endpoints, auth, message format) to unblock the bridge.
3. Claude (next session): build the Path-A Windows ES theme; flesh out real agent backends behind feature flags.
