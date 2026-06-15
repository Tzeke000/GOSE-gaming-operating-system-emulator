# GOSE Roadmap & Live Status

> Current as of **2026-06-15** (v0.6). Legend: ✅ done · 🟡 in progress · ⬜ todo ·
> 🔌 `[needs hardware]` · 🏗️ `[needs build]` (Linux host required)

---

## Done

### Shell & UI (live)
- ✅ ~50 `gose-*.html` pages: home/desktop, store, library, AI Hub, settings, wifi,
  bluetooth, OOBE, file manager, diagnostics, net monitor, padtest, parental controls,
  spectate, stress, GPU, cheevos, saves, friends, gallery, import/upload, task manager,
  terminal, and more.
- ✅ Boot sequence: `crystal-boot.html` (rotating ASCII crystal, generated from real mesh)
  → `gose-boot.html` (Core icon rise) → home or OOBE.
- ✅ GOSE Core crystal brand — boot splash, taskbar, every AI's presence indicator.
  Assets: `gui/mockup/assets/brand/gose-core*.svg|png` (Zeke's finished renders).
- ✅ Theme system: default Onyx + Midnight / Neon / Light, switchable in Settings.
- ✅ Widget standard (docs/21): `widget.js`/`widget.css` contract; desktop widgets + game bar.
- ✅ Windowing wave-1: `GoseWM` (`assets/gose-wm.js` + WinBox); System widget wired;
  `wm-test.html` static test harness. Full design in docs/23 (phases 2–3 pending).
- ✅ Crystal-shard software cursor (CSS kite polygon + gradient + facet theme).
- ✅ Kiosk freeze watchdog: JS heartbeat + server tick endpoint + kill-on-stale.
- ✅ Crash recovery / safe mode (docs/35): boot-success counter + known-good
  `gose-ui.prev` snapshot/auto-rollback + controller-navigable safe-mode page; 14-test suite.

### Controller & Input
- ✅ Controller standard (docs/27): one button language, one input path;
  `gose-pad-nav.py` bridges physical pads to the shell. Pages must not read the
  gamepad API directly.
- ✅ Input chooser (`input-select.html`) + platform/input model (`assets/platform.js`).
- ✅ Multi-input: gamepad focus-nav + gamepad pointer + mouse/kbd + PS5 DualSense.

### AI Permission Model (docs/16)
- ✅ Three tiers: `Observe / Play / Admin`; freshly paired AI gets `Observe` only.
- ✅ Grants enforced server-side on every call; revocation instant.
- ✅ Grant → token issuance wired: UI approval issues `ai_tokens.json` (SB-4.2).
- ✅ Owner credential = physical hold-✕ on the OS-admin controller; no dev-token
  shortcut for user-facing flows.
- ✅ Elevation sessions: 5-min sliding windows.
- ✅ `/ai/audit.jsonl` logs every AI op.
- ✅ Owner-gated privileged routes (e.g. `/controllers/admin`).

### AI Play Pipeline
- ✅ Seat manager: virtual pad binding per token; humans-first seating.
- ✅ `play.wait` push-call; Release button disarms.
- ✅ Play-map registry: baked into image (`agent/gose_agent/play_maps/`); schema
  documented in docs/32-play-map-registry.md.
- ✅ Per-game RAM profiles (`agent/gose_agent/profiles/`): pong1k2p verified live.
- ✅ RetroArch NCI (UDP RAM reads): reliable on a clean single launch (25/25 reads).
- ✅ Verified end-to-end: AI-vs-AI Pong (9-0); 14/14 breaker checks passed (2026-06-13).

### OOBE & First Boot
- ✅ `gose-oobe.html`: language → WiFi → user create → controller pairing → privacy
  (all off by default, opt-in). Pad drives the wizard.
- ✅ Deterministic OOBE gate: boots to OOBE when `.oobe-done` absent; redirects to
  home otherwise.

### Distribution & Packaging
- ✅ `build-gose-pc.sh`: bakes shell + agent from repo sources into clean image; SSH
  off, Samba off, `security.enabled=1`. Dry-run verified; real run `[needs build]`.
- ✅ `verify-image-clean.ps1`: fail-closed gate (rejects cred files + SSH-on state).
- ✅ `pc-image/dist/` distributable double-click bundle: `GOSE.bat` / `gose-launcher.ps1`
  / provision / decompression progress / console-hide after VM up / clean exit.
- ✅ CI workflow: `.github/workflows/build-image.yml` committed; live end-to-end run
  `[needs build]`.
- ✅ VERSION file as single source of truth (`0.6`).
- ✅ `global.autosave=1` ships by default (ADR-0014).
- ✅ License audit: docs/19 (findings frozen; ship-blocker review done).
- ✅ Hardened `batocera.conf.gose` seed; SSH/Samba off in shipped image.

### Infrastructure
- ✅ GOSE Agent: daemon (`python3 -m gose_agent`), client SDK + CLI, 181-test suite
  (stdlib-only), mock backends (no real `/dev/uinput` needed).
- ✅ MCP server (`mcp/`): zero-dep stdio; AI agents drive the device via `gose_*` tools.
- ✅ `gose_vm_server.py` shell server (port 8780); `kiosk.py` WebKit kiosk.
- ✅ Tailscale: agent 8731 + SSH 2222 served tailnet-only; hostfwd loopback-only.
- ✅ Stable-retro RAM map importer (~1,009 games).
- ✅ GOSE Agent over MCP (`mcp/` — docs/12 resolved: stdio transport).

---

## In Progress

- 🟡 **Windowing phases 2–3** — native-X-window integration, snap groups, overview
  (design approved in docs/23; implementation pending).
- 🟡 **CI end-to-end** — `build-image.yml` committed; live clean-image run on a Linux
  host not yet verified.
- ✅ **Global search** — apps + settings + files + **games + recently-played history** (gose-apps.html
  now reads /games.json + /recent.json; the home launcher's "Search apps & games" promise is fulfilled).
- ✅ **Gamepad nav consistency** — audited all 49 interactive pages (46 already navigable). Both REAL
  offenders fixed + verified: `gose-friends.html` + `gose-parental.html` got the spatial d-pad focus-nav
  pattern (`gose-parental`'s PIN pad was unnavigable → a controller user literally couldn't unlock it).
  The 3rd flagged page (`gose-upload.html`) is a PC/phone companion upload page (file-picker, opened
  off-device), NOT a controller surface — no nav needed.
- 🟡 **On-screen keyboard** — present; controller-driving the OSK is partial.

---

## Planned / Open

### Near-term (no hardware blocker)
- ⬜ **OTA update delivery** — no over-the-air mechanism yet.
- ⬜ **Zeke's hold-✕ OOBE walkthrough** — owner walk of the physical-presence path
  (pending owner availability; `[needs Zeke]`).
- ⬜ **Real `build-gose-pc.sh` end-to-end** on a Linux host → publish
  `gose-pc-x86_64.img.gz`. `[needs build]`
- ⬜ **Code-signing** the launcher/installer (unsigned `.bat`/`.ps1` trip SmartScreen).
- ⬜ **Steam listing path** — store assets, screenshots, depot upload, app config.
- 🟡 **Backup/restore + factory reset** — server side DONE + owner-gated + **test-covered** (10 tests:
  `gose_restore` archive-confinement, full backup↔restore round-trip proving roms/saves are never
  captured/touched, factory-reset gating). Remaining: the "Reset GOSE" / backup UI flow + USB/rclone
  destinations.
- ⬜ **i18n string layer** — extract strings from ~50 HTML pages to locale JSON before
  they multiply; ship en only.
- 🟡 **Audit log UI + encrypted AI credential** — full owner-facing audit viewer DONE
  (`gose-audit.html`: filterable all/allowed/denied log of every AI op, newest-first,
  controller-navigable, linked from the AI Players activity strip). Pending: the per-boot
  auto-connect encrypted credential (age/libsodium).
- ⬜ **Multi-account session switching** — accounts model exists; switch-user flow pending.
- ⬜ Notification center (richer), clipboard manager, in-game performance overlay (FPS).
- 🟡 Boot/shutdown animations — shutdown/restart screen DONE (`gose-shutdown.html`: crystal contract-out
  + "Shutting down…/Restarting…", power callers navigate there first). Boot animations already done; richer sound design pending.

### Needs hardware (Odin 2 — device not yet acquired)
- 🔌 **Odin 2 bring-up** — confirm variant on purchase; flash ROCKNIX to microSD; abl mod;
  first-boot checklist (controller, Wi-Fi, BT, GPU, audio). `[needs hardware]`
- 🔌 Real `uinput`/evdev input backends; verify RAM-map addresses on device.
- 🔌 HDMI / peripheral enumeration on the Odin 2.
- 🔌 Real battery/thermal/brightness sensors (VM reads host values; device has its own).
- 🔌 Per-game perf profiles + dock mode.
- 🔌 Simultaneous OTG + charging — unknown until hardware in hand.

---

## Repo & Working Rules

- **Dev branch: `main`** — commit and push to main; do NOT open a PR unless the owner asks.
- **Agent test gate:** `cd agent && py -3.11 -m unittest discover -s tests -v` (181 tests).
- **Host resilience test gate:** `cd pc-image/gose-vm-host && python3 -m unittest discover -s tests -v`
  (24 tests: watchdog crash-recovery 14 + backup/restore 10 = restore confinement 7, full backup↔restore
  round-trip, factory-reset gating; rollback/import cases need `rsync` + a Linux /userdata → full on the VM).
- **UI preview:** `python3 scripts/gose-preview.py`
- **VM dry-run:** `python3 scripts/gose_vm.py --dry-run`
- `[needs hardware]` = can only be validated on the Odin 2.
- `[needs build]` = needs a Linux host with network + root + qemu for real image build.

See `CLAUDE.md` for the full session workflow, open items, and packaging hazards.
