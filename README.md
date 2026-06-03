# GOSE — Gaming Operating System Emulator

Turn an **AYN Odin 2** into a console-like, controller-driven gaming + tinkering
device running a flashable Linux OS — with a Windows-style controller-only GUI,
broad emulation, universal controller support, and the ability to be **driven by
your AI agents (Ava, Wren, Iris)** over Wi-Fi or a cable.

> Not "write an OS from scratch." This flashes a mature handheld Linux distro
> (**ROCKNIX** or **Batocera**) to SD and *configures + extends* it. The few
> genuinely custom pieces are the Windows-like front-end, the **AI control
> agent**, and reproducible setup scripts.

## Status (2026-06)
- ✅ **Verified:** ROCKNIX is officially stable on the Odin 2 (all three variants);
  Batocera v42 supports it via the SM8550 image. Linux on the Odin 2 is real now.
- ✅ **Built this repo:** project memory, research, architecture, control protocol,
  and a working **GOSE Agent** (the daemon your AI controls the device through) —
  with mock backends so it runs and tests green off-device.
- 🔌 **Needs hardware:** flashing, real input injection, emulators, peripherals.
- 🧱 **Blocked on you:** the Ava/Wren/Iris API spec (to finish the AI bridge).

## Repo layout
| Path | What |
|------|------|
| `CLAUDE.md` | **Project memory** — read first; auto-loads each session. |
| `ROADMAP.md` | Live status checklist across all phases. |
| `docs/` | Brief, research + sources, install runbook, architecture, control protocol, GUI/controller plans, decision log. |
| `agent/` | **GOSE Agent**: device-side AI-control daemon + client SDK + CLI + tests. |
| `ai-bridge/` | Adapter mapping Ava/Wren/Iris ↔ the agent (reference skeleton). |
| `gui/` | Windows-like front-end work (theme/app). |
| `scripts/` | Reproducible, idempotent device setup. |

## Try the AI-control loop right now (no hardware)
```bash
cd agent
python3 -m unittest discover -s tests -v          # 20 tests, 0 deps
GOSE_AGENT_FORCE_MOCK=1 python3 -m gose_agent &    # start the daemon
python3 client/cli.py ping
python3 client/cli.py run "uname -a"               # AI "fixes the OS"
python3 client/cli.py tap a                        # AI "plays"
python3 client/cli.py launch psp "Some Game"
```
See `agent/README.md` and `docs/05-ai-control-protocol.md` for the full protocol,
and `docs/03-architecture.md` for how it all fits together (incl. the USB-cable
path via USB gadget networking).

## How "the AI controls the whole Odin 2"
1. **Play games** — the agent injects gamepad/keyboard events through a virtual
   `uinput` controller the emulators see as real, launches titles, and captures the
   screen so the AI can see and react.
2. **Fix the OS** — the agent runs shell, reports health, and manages services, so
   your AI can repair/tinker remotely over Wi-Fi/Ethernet or a USB cable.

## What I need from you (Zeke)
1. Confirm the exact Odin 2 variant (2 / Mini / Portal).
2. Greenlight **ROCKNIX-first** (recommended) vs Batocera-first.
3. Share how **Ava/Wren/Iris** expose themselves (endpoints, auth, message format)
   so the bridge can target a real API.
