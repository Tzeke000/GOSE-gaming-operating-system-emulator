# GOSE — Gaming Operating System Emulator

Turn an **AYN Odin 2** (Snapdragon 8 Gen 2 handheld) into a console-like,
controller-driven gaming + tinkering device running a **flashable Linux OS** — with
a **Windows-style, controller-only GUI**, broad emulation, universal controller
support, and the ability to be **driven by the owner's AI agents (Ava, Wren, Iris)**
over Wi-Fi or a cable. There's also a **downloadable PC version that runs as a
virtual machine**, so you can use GOSE before the handheld arrives.

> **This is not "write an OS from scratch."** GOSE flashes a mature handheld Linux
> distro (**ROCKNIX**) to SD and *configures + extends* it. The genuinely custom
> pieces are: the Windows-like front-end, the **AI control agent**, the boot/BIOS +
> login + input-chooser screens, and the reproducible build/setup scripts.

Owner: **Zeke (Tzeke000)** · created by **Ezekiel Angeles-Gonzalez**, Tzeke000 Studios.

---

## 🟢 New here? (humans and Claude Code sessions) start in this order
1. **`CLAUDE.md`** — project memory; auto-loads each session. Decisions + repo map + how-to. **Read it first.**
2. **`ROADMAP.md`** — live status checklist and what's next.
3. **`docs/04-decision-log.md`** — every decision (ADRs), newest first, with the *why*.
4. This README — the orientation you're reading.

Then prove the project runs locally (no hardware, no network):
```bash
pip install -r requirements-dev.txt          # only needed for the render scripts
python3 -m unittest discover -s agent/tests -v   # expect 79 passing
python3 scripts/gose-preview.py               # click through the UI in a browser
python3 scripts/gose_vm.py --dry-run          # see the GOSE-PC VM launch command
./pc-image/build-gose-pc.sh --dry-run         # see the image-build plan
```

**Working branch:** `claude/odin2-gaming-os-4SWOh` (default branch `main` mirrors it).
Develop there, commit with clear messages, push; don't open a PR unless asked.

---

## What GOSE is for
- **A console-like handheld OS** you flash to the Odin 2: boot into a clean,
  controller-driven, Windows-style desktop instead of fiddly menus.
- **Broad emulation** (PSP/PS2/N64/Switch/etc.) on a mature Linux base.
- **AI-operable**: your agents (Ava/Wren/Iris) — or Claude — can *play games* and
  *fix/tinker with the OS* remotely, over Wi-Fi or USB, through the **GOSE Agent**.
- **Usable today on a PC** as a virtual machine, before the hardware is in hand.

## How we got here (project history)
Built across one long session; full rationale in `docs/04-decision-log.md`.
- **Foundation** — research confirmed Linux is real on the Odin 2 (ROCKNIX stable;
  Batocera v42 via SM8550). Wrote the brief, architecture, and the **GOSE Agent**:
  a device-side daemon the AI drives (input injection, shell, game launch, status,
  screen capture) with **mock backends** so it runs/tests anywhere.
- **AI control** — newline-JSON-over-TCP protocol, a Python client SDK/CLI, an
  **MCP server** (Ava/Wren/Iris/Claude → tools), and a **game-state interface**
  ("Mineflayer for retro": read emulator RAM, no screenshots).
- **GUI** — navigable HTML prototypes + rendered concept PNGs for a Windows-like,
  controller-first experience: **boot splash → boot menu ("BIOS") → input chooser →
  login → desktop**. Theme system with a sleek-black **onyx** default (switchable in
  Settings). Vendored Inter font + Lucide icons.
- **Boot/BIOS** (ADR-0011) — hold **L1+R1** at power-on for a PC-style boot menu
  (pick OS, Recovery, Safe Mode, Fastboot, Setup); POST-style auto-boot countdown.
- **Distro decision** (ADR-0012) — **single Linux = ROCKNIX** dual-booted with stock
  **Android**; Batocera demoted to a documented fallback. Boot menu = ROCKNIX + Android.
- **GOSE on PC = a VM** (ADR-0013) — a *separate x86_64 image* (base **Batocera
  x86_64** + the GOSE layer) run in **QEMU**, not a web wrapper and not ARM emulation.
  Plus a **boot-time input chooser**: device defaults to Native (auto-accepts
  peripherals), the PC app defaults to Keyboard (can pick Controller).
- **Brand** — the GOSE logo (hexagon "G" + gamepad, violet→blue) wired into the boot
  splash, boot menu, login, and desktop. Credit: *by Ezekiel Angeles-Gonzalez ·
  powered by Tzeke000 Studios.*
- **Image build** — `pc-image/` scaffolds the real download: Batocera **42** (pinned)
  + GOSE layer → `.img` + importable `.ova`, with a sleek-black EmulationStation theme.

## Current state
- ✅ **Runs/tests green off-device** — 79 tests, zero required deps for the core.
- ✅ **GUI prototypes + concept renders** for every screen, on the onyx theme + logo.
- ✅ **GOSE-PC scaffolding** — VM launcher (`scripts/gose_vm.py`), image build
  (`pc-image/`), ES theme — all dry-run/tested.
- 🔌 **`[needs hardware]`** — flashing, real `uinput`/evdev input, emulators, HDMI,
  peripheral enumeration, on-device theme tuning. Marked as such in the docs.
- 🧱 **`[needs build]`** — run `pc-image/build-gose-pc.sh` on a Linux host (network +
  root + qemu) to produce the actual `GOSE-PC.img` / `GOSE-PC.ova`.
- ❓ **Blocked on Zeke** — the Ava/Wren/Iris API/transport spec (to finish `ai-bridge/`).

## Repo map
| Path | What |
|------|------|
| `CLAUDE.md` | **Project memory** — read first; auto-loads each session. |
| `ROADMAP.md` | Live status checklist across all phases. |
| `docs/` | Brief, research+sources, install runbook, architecture, control protocol, GUI/controller plans, **decision log (ADRs)**, boot menu, PC-app+input. |
| `agent/` | **GOSE Agent**: device-side AI-control daemon + client SDK + CLI + tests + game-state profiles. Mock backends run anywhere. |
| `mcp/` | Zero-dep **MCP server** — how Ava/Wren/Iris/Claude drive the device. |
| `ai-bridge/` | Adapter mapping Ava/Wren/Iris ↔ the agent (reference skeleton; needs their API). |
| `gui/mockup/` | Navigable **HTML prototypes** + concept PNGs + renderers: `boot`, `bootmenu`, `input-select`, `login`, `desktop`; `assets/themes.css`, brand logo. |
| `gui/theme-windows/` | Windows-like front-end notes. |
| `scripts/` | Device setup + mock-testable logic: `gose_bootmenu.py`, `gose_input.py`, `gose_vm.py` (VM launcher), `gose-preview.py` (UI preview). |
| `pc-image/` | **GOSE-PC image build**: `build-gose-pc.sh` (Batocera x86_64 + `gose-layer/` → `.img`/`.ova`), `make_ova.py`, the GOSE **ES theme**. |

## Try the AI-control loop (no hardware)
```bash
cd agent
python3 -m unittest discover -s tests -v          # tests, 0 deps
python3 -m gose_agent &                            # start the daemon (mock backends)
python3 client/cli.py ping
python3 client/cli.py run "uname -a"               # AI "fixes the OS"
python3 client/cli.py tap a                        # AI "plays"
python3 client/cli.py launch psp "Some Game"
```
See `agent/README.md` + `docs/05-ai-control-protocol.md` for the protocol, and
`docs/03-architecture.md` for how it fits together (incl. the USB-cable path).

## Next actions (see ROADMAP.md for the full list)
1. **Build the GOSE-PC image** on a Linux host: `sudo ./pc-image/build-gose-pc.sh`
   → produces the downloadable `.ova`. (Pinned to Batocera 42.)
2. **Open items for Zeke:** confirm the Odin 2 variant; share the Ava/Wren/Iris
   API/transport (stdio vs HTTP/SSE MCP, auth) to finish `ai-bridge/`.
3. **GUI polish:** per-system box art for the ES theme; push the carousel toward the
   Windows-tile look; build the "GOSE Setup (BIOS)" sub-screen.
4. **On hardware (when the Odin 2 arrives):** flash ROCKNIX, wire real
   `uinput`/evdev backends, validate HDMI/peripherals — all the `[needs hardware]` items.
