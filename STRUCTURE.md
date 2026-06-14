# STRUCTURE.md — what lives where (and what must not move)

> Current as of **2026-06-14** (v0.6).

The definitive repo map. One section per tree, load-bearing files called out,
and — critically — **deploy targets**: several trees are referenced *by exact
path* from outside this repo (the running VM host at `D:\gose-vm\`, the guest at
`/userdata/gose-ui/`, the owner's agent-home tooling, MCP registrations). Those
trees are marked **🔒 path-frozen**: organize them with READMEs/indexes, never
by moving or renaming files.

Reading order for newcomers: `CLAUDE.md` → `ROADMAP.md` → `docs/README.md`
(index of every numbered doc) → this file.

## Root

| File | What |
|------|------|
| `CLAUDE.md` | Project memory — auto-loads each session; decisions + how-to. Read first. |
| `README.md` | Orientation: vision, quickstart, history, repo map. |
| `ROADMAP.md` | Live status checklist across all phases. |
| `STRUCTURE.md` | This file. |
| `VERSION` | Single source of truth for the version number (`0.6`). |
| `requirements-dev.txt` | Dev-only deps (render scripts); the core agent has **zero** required deps. |
| `ruff.toml` | Lint config. |
| `.gitignore` | Python junk, images/ROMs, tokens/logs, node_modules. `pc-image/` has its own. |

## `agent/` — the GOSE Agent 🔒 path-frozen

Device-side AI-control daemon + client SDK + CLI + tests. Mock backends mean
everything runs/tests anywhere (no real `/dev/uinput` needed). External tooling
and the VM image reference these paths — do not move files.

- `gose_agent/` — the daemon (`python3 -m gose_agent`): `server.py`, `protocol.py`
  (newline-JSON v0, docs/05), `sandbox.py`, `capabilities/` (input / system /
  games / gamestate / screen).
- `gose_agent/profiles/` — per-game RAM maps for the game-state interface
  (docs/08): `pong1k2p.json` (verified live), `mario64.json`.
- `client/` — Python SDK (`gose_client.py`) + CLI (`cli.py`).
- `tests/` — the suite (**134 tests**, stdlib-only). Gate for every commit:
  `cd agent && py -3.11 -m unittest discover tests`.
- `tools/import_stable_retro.py` — imports stable-retro RAM maps (~1,009 games).
- `examples/pong_no_screenshots.py` — the no-screenshots play demo.

## `mcp/` — MCP server 🔒 path-frozen

`gose_mcp_server.py` — zero-dep stdio MCP server exposing the agent as `gose_*`
tools. Registered by exact path in external MCP configs (the owner's agents) —
do not move.

## `ai-bridge/` — adapter skeleton

`bridge.py` — thin AI-agents↔GoseClient adapter. Its README still says
"blocked on the agent spec"; that question was **resolved** in docs/12
(2026-06-04, MCP/stdio) — the MCP server in `mcp/` is the shipped answer, and
this skeleton remains as a reference for non-MCP transports.

## `docs/` — design + decisions

All numbered docs, indexed one-per-line with status in **`docs/README.md`**.
Highlights: `04-decision-log.md` (ADRs), `19-license-audit.md` (Steam
ship-blocker — findings are frozen), `27-controller-standard.md` (the input
law), `25-first-boot-oobe.md` (OOBE), `23-windowing-design.md` (approved
windowing plan). `asset-prompts/` = ready-to-paste AI prompts for brand
motion/audio assets (generated output goes to a gitignored `generated/`).

## `gui/` — the shell UI

- `mockup/` 🔒 **path-frozen — this IS the live OS UI.** Despite the historical
  "mockup" name, these HTML pages + `assets/` are **deployed into the guest at
  `/userdata/gose-ui/`** and rendered by the kiosk as the real GOSE shell.
  Load-bearing: `gose-home.html` (desktop), the `gose-*.html` apps (settings /
  store / files / library / OOBE / wifi / bluetooth / …), `assets/themes.css`
  (theme tokens), `assets/widget.js`+`widget.css` (docs/21 widget standard),
  `assets/gose-wm.js` + `assets/vendor/winbox/` (windowing), `assets/sound.js`
  + `assets/sounds/` (docs/26), `assets/platform.js`, `assets/icons/brand/`
  (the owner's icon set), `assets/fonts/` (vendored Inter + LICENSE).
  `render_*.py` + `*-concept.png` are the early concept renderers/renders
  (historical, harmless). One file per page; pages must NOT read the gamepad
  directly (docs/27 §2.3).
- `theme-windows/` — **historical stub** (README only): the early
  EmulationStation-theme plan from docs/06. The shipped shell became the web
  kiosk instead; the ES theme that *was* built lives at
  `pc-image/gose-layer/themes/gose/`. Kept for the record.

## `pc-image/` — GOSE-PC image build + VM host tooling

- `build-gose-pc.sh` — image orchestrator (Batocera x86_64 + gose-layer →
  `.img`/`.ova`); `--dry-run` works anywhere. `make_ova.py` — OVA packager
  (unit-tested). `render_theme_preview.py` — ES-theme preview render.
- `gose-layer/` — files injected onto Batocera userdata: `system/` (agent
  autostart `custom.sh`, `batocera.conf.gose`, baked-apps provisioning),
  `themes/gose/` (the ES theme + assets), `splash/`.
- `gose-vm-host/` 🔒 **path-frozen — the most deploy-sensitive tree in the
  repo.** Backup/version-history copies of the scripts that actually run: on
  the Windows host at `D:\gose-vm\` (`boot-gose-vm.ps1`, `host_bridge.py`,
  `pad_passthrough.py`, `capture.ps1`, `elev_agent.ps1`/`elev_launch.bat`,
  `gamecontrollerdb.txt` — vendored, do not edit) and inside the guest at
  `/userdata/gose-ui/` (`gose_vm_server.py` — the shell's server brain,
  `kiosk.py` — the WebKit kiosk, `gose-pad-nav.py` — the docs/27 input bridge,
  `watchdog.py`, `overlay_window.py`, `gose-session.sh`, `start-shell.sh`,
  storage rules/handler). Helper push/inspect scripts (`push_library.py`,
  `swap_shell.py`, `reload_ui.py`, `peek_files.py`, `agent_probe.py`,
  `e2e_check.py`, …) are host-side dev tools. External runbooks reference these
  exact paths — never move or rename here.
- `dist/` — the distributable double-click bundle: `GOSE.bat`,
  `launcher/gose-launcher.ps1` + icon, `package-bundle.ps1` (assembles the
  bundle, copying runtime scripts **from `../gose-vm-host/` at package time** —
  canonical source stays there), `make-shortcut.ps1`. Build outputs (`qemu/`,
  `vm/`, images) are gitignored.

## `scripts/` — device setup + mock-testable logic

`setup-device.sh` / `install-agent.sh` (on-device setup), `gose_bootmenu.py`
(boot-menu trigger, docs/10), `gose_input.py` (input chooser, docs/11),
`gose_vm.py` (QEMU launch builder, `--dry-run`), `gose-preview.py` (zero-dep
browser preview of the UI).

## `.claude/` 🔒 path-frozen

`settings.json` + `hooks/session-start.sh` — SessionStart primer + agent
self-test. Dev branch is `main` (historical branch retired); docs/12 resolved
the agent connection spec (MCP/stdio).

## What ships where (deploy summary)

| Tree | Ships to |
|------|----------|
| `gui/mockup/**` | guest `/userdata/gose-ui/` (the live shell) |
| `pc-image/gose-vm-host/*` (guest set) | guest `/userdata/gose-ui/` |
| `pc-image/gose-vm-host/*` (host set) | Windows host `D:\gose-vm\` |
| `pc-image/gose-layer/**` | baked into the built image (userdata) |
| `agent/**` | on-device daemon (and runs locally for tests) |
| `mcp/`, `ai-bridge/` | the AI-agent side (host/agent-home) |
| `pc-image/dist/**` | the downloadable bundle (assembled by `package-bundle.ps1`) |
| `docs/`, `*.md` | humans (and session priming) — nothing deploys |
