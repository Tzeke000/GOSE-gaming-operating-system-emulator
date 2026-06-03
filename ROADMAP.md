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
- ⬜ **Both in parallel:** flash ROCKNIX (card A) + Batocera SM8550 (card B); abl
      mod; boot from SD (runbook §D)
- ⬜ First-boot checklist on each (controller nav, Wi-Fi, BT, GPU, audio) (§F)
- ⬜ Bench PSP/PS2/Switch on both → pick daily driver

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
- 🟡 Plan written (`docs/06-gui-plan.md`); theme stub started
- ⬜ Path A: Windows-style ES theme (home→system→library→launch, controller-only)
- ⬜ Tools area (terminal, file manager, network tools, AI bridge launcher)
- ⬜ Evaluate Path B (Godot custom front-end) where theme falls short

## Phase 6 — AI control `[CUSTOM]`
- ✅ GOSE Agent: input/system/games/screen capabilities (mock-backed)
- 🔌 Real backends on device (uinput pad, framebuffer capture, real game launch)
- 🔌 USB-cable path (USB gadget networking → `usb0`)
- 🧱 AI bridge mapping **Ava/Wren/Iris** ↔ GOSE — blocked on their API spec (Zeke)

## Phase 7 — Reproducibility
- 🟡 `scripts/setup-device.sh` grows with every customization
- ⬜ Full "re-flash → one script → restored" validated on hardware

## Immediate next actions
1. Zeke: confirm Odin 2 variant + whether to start with ROCKNIX (recommended).
2. Zeke: share the Ava/Wren/Iris API (endpoints, auth, message format) to unblock the bridge.
3. Claude (next session): build the Path-A Windows ES theme; flesh out real agent backends behind feature flags.
