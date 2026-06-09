# 17 — What GOSE Still Needs (roadmap) `[CUSTOM]`

> Status: **2026-06-05 (at the owner's request: "make a whole list of things the OS needs").**
> Honest inventory of the gaps between where GOSE is now and a complete, distributable OS.
> Legend: ✅ done · 🟡 partial · ⬜ not started · ⭐ high priority.

## Baseline already built (so the list below is only what's LEFT)
GOSE shell (sole web kiosk, GPU-rendered) · boot/splash/lock · Settings (Windows-style:
Personalization, Display, Sound, **Network & internet**, **Bluetooth & devices**, **Controllers**,
Input, Time, Power, Privacy, Accounts, AI & Remote, About) · **Wi-Fi** scan/join/forget (real, via
host netsh) · **Bluetooth** pair/connect (real radio) · power actions (sleep/restart/shutdown) ·
Files · Task Manager · Terminal · Store (queue) · Splice · Storage · retro emulation + Steam/Proton
installed · game library w/ art + playtime · audio passthrough · notifications · widgets · screen
capture/stream · **AI Hub** (presence + permission tiers + revoke; enforcement engine live).

---

## A. AI layer — last mile ⭐ (engine done; wiring left)
- ⬜⭐ **Grant → token issuance:** granting a tier in the Hub UI must *issue* that AI's agent token
  (write `ai_tokens.json`) so the enforcement actually applies. (Engine + UI both exist; connect them.)
- ⬜⭐ **Encrypted AI-side credential** + auto-connect each boot (age/libsodium; the AI holds it).
- ⬜ **Pairing/device-grant flow:** AI requests access → human approves → token minted (RFC 8628 shape).
- ⬜ **Audit log** of what each AI did (per docs/16; Capframe gives signed receipts).
- ⬜ Capframe (or macaroon) upgrade for holder-of-key + attenuation (docs/16 phase 3).

## B. Desktop / windowed multitasking ⭐ (biggest architectural lift)
- ⬜⭐ GOSE is a single-app kiosk today. Needs a **window manager**: run apps in movable/resizable
  windows, Alt-Tab/switcher, multitask.
- ⬜⭐ **The owner's widget↔window model:** widgets and app-windows as one continuum — *maximize a widget
  into a full window*, and when you "act out" of it, it **suspends and frees memory** like closing an
  app. (Memory-aware windowing, not just z-order.)
- ⬜ Drag-and-drop between windows; clipboard manager.

## C. Multiplayer (human + AI) 
- ⬜⭐ Human + AI in the **same game** (split controller: P1 human, P2 = AI input injection).
- ⬜ AI-vs-AI matches (no human seat); co-op.
- ⬜ Over **LAN or Bluetooth**; join/host flow; **control arbitration** (who owns input when shared).

## D. Hardware / the real Odin 2 (device target)
- ⬜⭐ The **ARM device build** (Odin 2 not purchased yet): Box64/FEX + Wine + Proton for modern games on ARM.
- ⬜ Real peripherals on hardware: controller, BT, Wi-Fi, **real battery/thermal/brightness sensors**
  (the dials read host values in the VM; need the device's own sensors).
- ⬜ Flashable **device image** + install runbook.

## E. Distribution / packaging ⭐ (owner: "an app anyone can download, maybe Steam")
- 🟡 **Flashable image / installer** anyone can download (Batocera ships this way; build a GOSE image).
- 🟡 PC installer / runnable build decoupled from our dev machine — **VM launcher BUILT** (see below).
- ⬜ **Update mechanism** (OTA / versioned updates) — currently none.
- ⬜ **Steam listing** path (emulation frontends do live on Steam); store assets, screenshots.
- 🟡 OOBE polish (brand-agnostic, name-your-own-AI — already designed; finish + test the full first-run).

### Distribution launcher — BUILT (2026-06-06) `pc-image/dist/`
The "downloaded GOSE from Steam/GitHub → double-click → it runs in its own VM" experience.
GOSE boots **inside a QEMU VM**, so installing/running it **never converts the user's real
Windows machine**, and launching needs **no admin**.

**Built (working local launcher):**
- `pc-image/dist/GOSE.bat` — double-click entry; runs the launcher with a console title "GOSE".
- `pc-image/dist/launcher/gose-launcher.ps1` — orchestrator. **Reuses `boot-gose-vm.ps1`** (the working
  virgl GPU + audio + Bluetooth + host-bridge boot); does NOT reimplement QEMU args. Flow:
  detect-already-running → focus the GOSE window instead of double-booting; else first-run
  **provision** (decompress `vm/gose-disk.img.gz` once) → boot via the reused script pointed at
  bundle-relative QEMU + image (via `GOSE_QEMU_BIN`/`GOSE_IMAGE`/`GOSE_BRIDGE` env overrides, defaults
  unchanged for the dev box) → wait for the in-VM agent on `127.0.0.1:8731` → bring the GOSE window forward.
- `pc-image/dist/make-shortcut.ps1` — creates a desktop `GOSE.lnk` with the **GOSE Core `.ico`**
  (`launcher/gose.ico`, rasterized from `gui/mockup/assets/brand/gose-core-mark.png`).
- `pc-image/dist/package-bundle.ps1` — assembles the shippable folder from canonical pieces
  (launcher glue + `gose-vm-host` runtime scripts + portable QEMU + the image).
- `pc-image/dist/README.md` — the user-facing "double-click GOSE to start" + bundle layout.

**Bundle layout (what a user downloads):**
```
GOSE\  GOSE.bat · make-shortcut.ps1 · README.md
       launcher\ gose-launcher.ps1 · boot-gose-vm.ps1 · host_bridge.py · gose.ico
       qemu\     (portable QEMU: qemu-system-x86_64.exe + DLLs)
       vm\       gose-disk.img(.gz)
```
The large payloads (`qemu/`, `vm/`, the copied runtime scripts) are **assembled at package time**,
not committed (the repo holds the launcher glue + icon only).

**Verified end-to-end (2026-06-06):** stopped the running VM → ran the launcher → it booted via the
reused script (host bridge + QEMU + BT bridge up) → agent `gose_ping` → pong → clean first-boot state
preserved (`.oobe-done` absent, no `accounts.json`) → kiosk routes to `gose-oobe.html` (deterministic:
`gose-session.sh` lands OOBE when the flag is absent + `gose-boot.html` redirects on `/oobe/status !done`).
Single QEMU instance, no duplicates.

**Launcher window polish (2026-06-09, task #17 follow-up):**
- ✅ **VM window title** — `boot-gose-vm.ps1` now sets `$env:SDL_VIDEO_WINDOW_TITLE = 'GOSE'` before
  launch; SDL inherits it and the QEMU window shows "GOSE" instead of "QEMU (GOSE-PC)".
- ✅ **No spurious console window from QEMU** — `boot-gose-vm.ps1` now prefers `qemu-system-x86_64w.exe`
  (the Windows-subsystem variant, present in MSYS2; falls back to `.exe` if absent). The windowed
  variant does not open a secondary console, so only the GOSE VM window is visible to the user.
- ✅ **First-run decompression progress** — chunked streaming with MB/s + bytes-written feedback every
  32 MB; user sees progress during the ~1 min first-run provision instead of a frozen spinner.
- ✅ **Console hide after VM is up** — once the agent answers and the VM window is focused,
  `Hide-Console` (Win32 SW_HIDE via P/Invoke) hides the launcher console. Only the GOSE window
  remains on screen. Non-fatal if the P/Invoke fails (console stays visible, harmless).
- ✅ **Clean exit** — launcher now calls `WaitForExit()` on the QEMU process. When the user closes the
  GOSE window, the launcher exits and GOSE.bat's console also disappears — no ghost windows.

**Still TODO (honest — this is a working LOCAL launcher, not a store package):**
- ⬜⭐ **Real Steam depot packaging** (depot upload, app config, Steam launch options) + store assets.
- ⬜⭐ **Code-signing** the launcher/installer (unsigned `.bat`/`.ps1`/`.exe` trip SmartScreen/AV).
- ⬜ **Auto-update** of the VM image + launcher (no OTA yet).
- ⬜ **Trim portable QEMU** to the `qemu-system-x86_64` DLL closure (current `package-bundle.ps1` copies
  the whole MSYS2 `mingw64\bin` — over-broad, hundreds of MB).
- ⬜ **WHPX dependency**: QEMU acceleration needs Windows Hypervisor Platform; enabling it is a one-time
  admin host step. Launching GOSE afterward needs no admin — but the bundle should detect+guide this.
- ⬜ **Reboot-verify (with Zeke)**: after a cold VM reboot, verify boot splash chain is correct
  end-to-end (S03 → S28 → kiosk) — not testable without rebooting the live VM. Owned by Zeke.
- ⬜ A native `.exe` wrapper (vs `.bat`) for a cleaner Steam/desktop launch + a proper splash UI.

## F. OS fundamentals still thin
- ⬜ **Update/OTA** (see E). ⬜ Clipboard manager. ⬜ VPN. ⬜ Mobile hotspot/tethering.
- 🟡 Global search (apps+settings+files done; add games + history). 🟡 Notification center (basic → richer).
- ⬜ Multi-account **session switching** (accounts model exists; add switch-user). ⬜ Real suspend/resume.
- ⬜ Accessibility (text scale, high-contrast, colorblind palettes). ⬜ Printing (low priority on handheld).

## G. Game experience
- ⬜⭐ **Save states + cloud/backup saves**. ⬜ **RetroAchievements** integration.
- 🟡 Per-game settings/overlay (Game Bar exists — extend: shaders, filters, per-game controller profiles).
- ⬜ Favorites / collections / "continue playing". ⬜ In-game performance overlay (FPS).

## H. Polish / UX
- 🟡 **Consistent gamepad nav on every page** (some are keyboard-first; unify the input model).
- 🟡 On-screen keyboard everywhere (done; controller-driving the OSK is partial).
- ⬜ Boot/shutdown animations; richer sound design; full theme coverage (BIOS/OOBE still TODO on a few).

## I. Security for distribution
- 🟡 **AI permission enforcement** (engine ✅; last-mile A above).
- ⬜ Sandbox untrusted/3rd-party AIs. ⬜ Per-user data isolation. ⬜ Safe out-of-box defaults audit.

---

## Suggested order (my read)
1. **Finish the AI layer last-mile (A)** — small, and it's the keystone for distribution.
2. **Desktop/windowing (B)** — the biggest unlock; everything multitask-y depends on it; the owner's
   widget↔window-memory model is the design.
3. **Save states + achievements (G)** — high player value, mostly integration not invention.
4. **Packaging + updater (E)** — turn it into something downloadable; needed before Steam.
5. **Multiplayer (C)** and **the Odin 2 build (D)** — the headline features, gated on hardware (D) and
   on windowing/arbitration (C).
6. **Polish + accessibility (F, H, I)** — continuous; tighten before any public release.

Related: `docs/14-ai-hub.md`, `docs/16-ai-permission-model.md`, `gose_production_stack.md`,
runbook `<agent-home>\notes\gose_vm_runbook.md`, memory `project_gose_distributable_2026-06-05`.

## J. Gaps found by comparison with shipped OSes (gap-analysis fan-out, 2026-06-06)

Compared against SteamOS / Switch / Batocera / ROCKNIX / muOS / ES-DE. Verified reuse, not memory.
Status 2026-06-06: multiplayer seats (C) DONE; save states (G) DONE; AI last-mile (A) in flight.

1. **Crash recovery + safe mode (M)** — boot-success counter in watchdog; 3 failed shell starts →
   auto-restore previous gose-ui + a minimal safe-mode page. No story today for a bricked UI push.
2. **Backup/restore + factory reset (M)** — tar /userdata/gose-ui + accounts + saves to USB/rclone;
   "Reset GOSE" wipes config not ROMs. Boot-menu mentions factory reset as concept only.
3. **License hygiene for Steam (M, BLOCKING for paid listing)** — Snes9x libretro core is
   NON-COMMERCIAL (verified docs.libretro.com); audit all bundled cores (scancode-toolkit, Apache-2.0),
   attribution screen, no-piracy defaults posture.
4. **Crash reporting + diagnostics export (S, opt-in)** — sentry-sdk → self-hosted GlitchTip later;
   tonight-able: "Export diagnostic bundle" (logs+health) from Settings.
5. **Parental controls / kid mode (M)** — PIN-gated account switch on the existing lock screen +
   kidgame gamelist tag allowlist (Batocera ui_mode / ES-DE kid mode pattern; accounts + playtime
   already exist as substrate).
6. **Screenshots gallery + recording (M)** — RetroArch NCI SCREENSHOT / RECORDING_TOGGLE (verified
   live) + gose-gallery.html; Game Bar buttons.
7. **In-OS help + public docs (S)** — hold-Guide controller-glyph legend per page; MkDocs site from docs/.
8. **CI pipeline (S)** — GitHub Actions Linux runner: agent tests + build-gose-pc.sh (root+loop mounts)
   → publish .img/.ova to Releases. Solves the "real build needs a Linux host" crux in the distribution plan.
9. **i18n string layer (M)** — extract strings from the ~18 HTML pages to locale JSON before they
   multiply further; ship en only.
10. **Per-game perf profiles + dock mode (M, DEVICE-GATED)** — fold into the Odin 2 plan.

Remote access: **Tailscale is LIVE on the host (2026-06-06)** — agent 8731 + SSH 2222 served
tailnet-only (TLS); hostfwd now loopback-bound (was 0.0.0.0 = LAN-exposed, fixed) + firewall block
rule as second layer. Odin 2: ROCKNIX ships a built-in Tailscale toggle. In-guest pattern for the
shipped image: static tailscaled under /userdata + batocera-services script (tun via mknod each boot,
or --tun=userspace-networking).
