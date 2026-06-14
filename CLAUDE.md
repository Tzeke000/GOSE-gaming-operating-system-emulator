# CLAUDE.md — GOSE project memory (read me first, every session)

> This file is auto-loaded at the start of every Claude Code session. It is the
> project's **persistent memory**: keep it current. When you make a meaningful
> decision, log it in `docs/04-decision-log.md` and update the relevant section here.
> Current as of **2026-06-14**.

## What this project is

**GOSE = Gaming Operating System Emulator.** A Batocera-based QEMU virtual machine
with a fully custom browser-shell UI, a permission-tiered AI-control layer, a local
multiplayer seat manager, and a play pipeline that lets an AI join a game as a real
player.

The **shell** is ~50 `gose-*.html` pages served by `gose_vm_server.py` (port 8780,
loopback-only) and rendered by `kiosk.py` (WebKit kiosk). "Mockup" in the path name
is historical — these pages ARE the live OS. They deploy to `/userdata/gose-ui/` in
the guest. The guest also runs the GOSE Agent on port 8731 (token-gated).

**Handheld target:** the same GOSE layer will run on an **AYN Odin 2** (Snapdragon 8
Gen 2) under **ROCKNIX** (Linux on microSD, Android stays on internal). Device not
yet acquired — keep code variant-agnostic.

Full original brief: `docs/00-project-brief.md`.

## Current decisions (append-only; see `docs/04-decision-log.md` for full ADRs)

| ADR | Decision | Status |
|-----|----------|--------|
| ADR-0014 | `global.autosave=1` ships by default (auto-resume on every libretro game) | accepted, live |
| ADR-0013 | GOSE on PC = a QEMU VM (Batocera x86_64 + GOSE layer); boot-time input chooser | accepted; image build needs a Linux host |
| ADR-0012 | Single Linux = ROCKNIX (dual-boot + Android); Batocera = documented fallback | accepted |
| ADR-0011 | GOSE Boot Menu ("BIOS") on L1+R1 at power-on; POST-style countdown | accepted; I/O at hardware bring-up |
| ADR-0010 | Onyx (sleek black) default theme + Midnight/Neon/Light alternates | accepted, live |

**Device not yet acquired.** Stay variant-agnostic (Odin 2 / Mini / Portal all viable).
Mark anything that can't be validated here as `[needs hardware]`.

## Verified facts (2026-06; see `docs/01-research-findings.md` for sources)

- ROCKNIX: officially stable on all three Odin 2 variants. Boots from microSD.
- Batocera v42 (SM8550 image): good emulation coverage; the PC-VM uses Batocera x86_64.
- Both need a one-time bootloader (abl) modification to boot Linux off SD.
- GPU accel = Freedreno/Turnip (Vulkan). Wi-Fi + Bluetooth work.
- **VM GPU ceiling:** modern Steam/Wine is structurally impossible in the VM (no GPU
  passthrough on Windows host — VFIO is Linux-only; no Vulkan/DXVK). The VM handles
  retro + light/old-GL games. Don't re-litigate; don't promise modern Steam on the VM.
- Known hardware gotcha: dock HDMI on Linux can fail (driver gaps) — keep USB-C→HDMI.

## How the shell runs (the live VM stack)

```
boot-gose-vm.ps1 (Windows host)
  → QEMU (Batocera x86_64, virtio-gpu-gl)
    → boot-custom.sh patches ES launch line (idempotent, runs every boot)
    → Openbox WM → kiosk.py (WebKit, renders the gose-*.html pages)
    → gose_vm_server.py (shell server, port 8780 loopback)
    → gose-pad-nav.py (docs/27 input bridge: physical pads → shell nav events)
    → watchdog.py (JS heartbeat + kill-on-stale; kiosk freeze protection)
    → GOSE Agent (gose_agent, port 8731, token-gated)
```

**Controller flow (docs/27 — the law):** physical pads arrive via `pad_passthrough.py`
(input-level passthrough, host side). The shell reads pad events only through the
`gose-pad-nav.py` bridge. Pages must NEVER read the gamepad API directly (that's a
bug; flag it).

**Port map:**
| Port | Service | Who reaches it |
|------|---------|----------------|
| 8780 | `gose_vm_server.py` (shell server) | loopback only (kiosk + scripts) |
| 8731 | GOSE Agent | host loopback + tailnet (token-gated) |
| 2222 | SSH (dropbear) | host loopback + tailnet only |

**The revert hazard:** `watchdog.py` monitors the kiosk heartbeat. If the kiosk stalls
long enough, it kills and restarts the kiosk — which reloads from disk. Any UI change
you push must already be on disk or it will be reverted on the next watchdog cycle.
Always use `reload_ui.py` (or `push_library.py` / `inject_gose_layer.py`) to push
before testing; don't assume an in-memory state survives a watchdog cycle.

## Boot sequence + brand

1. **`crystal-boot.html`** — stage 1: pure-black screen, rotating ASCII GOSE Core
   crystal, scales to fill any viewport. `crystal-frames.js` supplies the frame data;
   regenerate it with `tools/crystal_ascii.py` if the mesh changes.
2. **`gose-boot.html`** — stage 2: GOSE Core icon rise + glow animation, themed.
3. **Home or OOBE** — if first-boot (`gose-oobe.html`); otherwise `gose-home.html`.

**Brand assets** (`gui/mockup/assets/brand/`):
- `gose-core.svg` / `gose-core.png` — full mark (crystal + halo + ring); use for boot
  splash and large hero spots.
- `gose-core-mark.svg` / `gose-core-mark.png` — crystal only, square viewBox; use for
  headers, taskbar Start, login, and the tiny Core beside each AI's name.
- `gose-crystal.png` / `gose-logo.png` — Zeke's finished renders (two-background matted).
- SVG assets are crisp at any size and animatable. See `docs/15-brand.md`.

## AI permission model (implemented; docs/16)

Three tiers, enforced server-side on every call:
- **Observe** (`gose:observe`) — read-only: screenshot, list games, read game state.
  Default for a freshly paired AI.
- **Play** (`gose:play`) — launch/stop games, send controller input.
- **Admin** (`gose:admin`) — full OS: shell, settings, install, network, power.

Grants live in `ai_grants.json` / `ai_tokens.json` (NOT committed to the image — the
`verify-image-clean.ps1` gate rejects any image that carries them). An AI can never
self-elevate. The owner credential is a **physical hold-✕ on the OS-admin controller**
— there is no dev-token shortcut for user-facing flows (`feedback_user_sovereignty`).
Elevation sessions are 5-min sliding windows. `/ai/audit.jsonl` logs every AI op.

## Play pipeline (implemented)

1. AI pairs → gets `Observe` token.
2. Owner elevates to `Play` via hold-✕.
3. AI calls `games.playmaps` → picks a game → `games.playmap {id}` → reads the play map.
4. AI arms a seat: `POST /ai/seat` → virtual controller bound at grant time.
5. `POST /launch` → game starts; AI reads board over RetroArch NCI (UDP 127.0.0.1:55355).
6. `play.wait` (push-call) notifies AI when the game is ready; Release button disarms.

Play maps live in `agent/gose_agent/play_maps/` (baked into the image). Schema:
`id`, `name`, `system`, `controls`, `ram_fields`, `game_flow`. Cross-links a
`ram_profile` in `agent/gose_agent/profiles/`.

Verified: AI-vs-AI Pong clean (25/25 NCI reads); 14/14 breaker checks passed
(2026-06-13 ship audit). NCI is reliable on a clean single launch; "flaky NCI" = multiple
emulator instances or rushed relaunches, not inherent fragility.

## Packaging + ship path

1. **Build** (Linux host required): `sudo ./pc-image/build-gose-pc.sh`
   - Bakes shell (`gui/mockup/*.html` + `assets/`) + guest runtime (`gose-vm-host/` guest set)
     into the image at build time. No manual push step.
   - Hardened `batocera.conf.gose`: SSH off, Samba off, `security.enabled=1`.
   - Output: `pc-image/build/gose-pc-x86_64.img.gz`.
2. **Gate**: `pc-image/verify-image-clean.ps1` — fail-closed; rejects if any cred file
   or SSH-on state is present. Called automatically by `package-bundle.ps1`.
3. **Package** (Windows): `pc-image/dist/package-bundle.ps1`
   - Defaults `-ImageGz` to the clean build output (NOT the dev disk).
   - Assembles the `dist/` bundle: `GOSE.bat`, `gose-launcher.ps1`, icon.
4. **Untested seam:** a real `sudo build-gose-pc.sh` + `package-bundle.ps1` end-to-end
   on a Linux host has not been run. That is the conclusive test.

**DO NOT** ship the dev disk (`D:\gose-vm\batocera-x86_64-*.img.gz`) directly — it
carries SSH on + root pw "linux" + the owner token + dev session state.

## Repo map

- `docs/` — numbered design docs + `docs/README.md` index (every doc, one line, status).
  Key docs: `04` (ADRs), `15` (brand), `16` (AI permissions), `19` (license audit),
  `21` (widget standard), `23` (windowing design), `25` (OOBE), `27` (controller standard),
  `31` (security), `32` (build bakes the shell), `33` (prep-for-ship).
- `agent/` 🔒 — GOSE Agent daemon + client SDK + CLI + 181-test suite + RAM profiles +
  play-map registry. **Path-frozen** (external tooling + MCP configs reference by exact path).
- `mcp/` 🔒 — zero-dep stdio MCP server (`gose_mcp_server.py`). **Path-frozen.**
- `gui/mockup/` 🔒 — **the live shell UI**. HTML pages + assets deploy to guest
  `/userdata/gose-ui/`. **Path-frozen** (kiosk + server reference exact paths).
- `pc-image/gose-vm-host/` 🔒 — guest + host runtime scripts. **Path-frozen** (the most
  deploy-sensitive tree — external runbooks reference exact paths).
- `pc-image/dist/` — distributable bundle.
- `pc-image/gose-layer/` — files injected onto Batocera userdata (agent autostart,
  batocera.conf.gose seed, ES theme).
- `scripts/` — device setup + mock-testable logic; QEMU launcher; UI preview.
- `STRUCTURE.md` — full what-lives-where map with deploy targets.

## How to work in this repo

- **Dev branch: `main`** (the historical `claude/odin2-gaming-os-4SWOh` branch is
  retired, 61+ commits behind). Develop, commit, push to main. Do NOT open a PR
  unless the owner asks.
- **Agent test suite** (stdlib-only, no deps):
  `cd agent && python3 -m unittest discover -s tests -v` (expect 181 passing).
- **UI preview** (zero-dep): `python3 scripts/gose-preview.py`
- **VM dry-run**: `python3 scripts/gose_vm.py --dry-run`
- **Image build dry-run**: `./pc-image/build-gose-pc.sh --dry-run`
- **Agent mock mode**: `cd agent && python3 -m gose_agent`; drive with
  `python3 client/cli.py ping` (from `agent/`).
- `[needs hardware]` = can only be validated on the Odin 2. Keep a flag; don't stub out.
- `[needs build]` = needs a Linux host with network + root + qemu for real image build.

## Open items

- CI build (`build-image.yml`) — workflow committed; live end-to-end run not verified.
- OTA update delivery — not designed yet.
- Windowing phases 2–3 (native-X integration, snap groups, overview) — design in docs/23.
- Zeke's hold-✕ OOBE walkthrough — pending owner availability.
- Odin 2 variant + purchase — device not yet acquired; confirm on arrival.
- Odin 2 simultaneous OTG + charging — unknown until hardware in hand.
