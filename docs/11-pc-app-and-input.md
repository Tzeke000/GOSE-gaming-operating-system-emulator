# 11 — GOSE on PC (virtual machine) + boot-time input chooser

> Zeke (2026-06): "Make GOSE a downloadable PC app — I'll use that first until I
> get the Odin 2 — and it should be **more like a virtual machine**. At boot you
> pick how to navigate; default is native on the device, keyboard on PC, and it
> accepts peripherals (keyboard/controller, Bluetooth/dongle/wired)."

## GOSE on PC = a virtual machine (not a web wrapper, not ARM emulation)
GOSE runs in two places from one distro-agnostic custom layer (front-end + agent +
input model):

| Target | Base | Arch | How it runs |
|--------|------|------|-------------|
| **Device** | ROCKNIX | ARM64 | flashed to microSD on the Odin 2 (ADR-0012) |
| **PC app** | Batocera x86_64 | x86_64 | **GOSE-PC image booted in a QEMU VM** |

Why a VM (and why a *separate x86 image*):
- A web/Electron wrapper would only show the UI — not the real OS, emulators, or
  agent. A VM runs the **actual GOSE environment**, so the PC app is a faithful
  preview of the handheld.
- Emulating the **ARM** device image on an x86 PC is far too slow (emulator inside
  an emulated CPU). So GOSE-PC is a **native x86_64 build** running with hardware
  acceleration — near-native speed.
- Our custom layer is arch/distro-agnostic, so the *same* front-end/agent/theme
  sits on ROCKNIX (device) and Batocera x86_64 (PC) → one experience, two builds.

### Engine: QEMU (bundled)
QEMU is scriptable, cross-platform, and bundleable. Accelerator auto-detected:
**KVM** (Linux), **HVF** (macOS), **WHPX** (Windows), `tcg` fallback. Display via
**virtio-gpu-gl**; user-mode networking forwards TCP **5555** so the GOSE agent
(and Ava/Wren/Iris) is reachable exactly like over Wi-Fi on the device. A host
folder mounts into the guest (virtio-9p, tag `gose-share`) for ROMs/saves.

Launcher: **`scripts/gose_vm.py`** (command builder + accel detection are
unit-tested in `agent/tests/test_vm.py`):
```
python3 scripts/gose_vm.py --dry-run                      # show the QEMU command
python3 scripts/gose_vm.py --share ~/roms --controller 046d:c21d   # real boot [needs image]
```
Booting for real needs the **GOSE-PC image** + a host QEMU. Building/publishing
that image (Batocera x86_64 + GOSE layer, as `.img` and an importable `.ova`) is
the next milestone — **[needs build]**. VirtualBox/VMware import documented as
manual alternatives.

### See the UI right now (no image needed)
`scripts/gose-preview.py` serves the HTML front-end and opens the boot flow as the
PC app — handy for iterating on the UI before the VM image exists:
```
python3 scripts/gose-preview.py      # boot -> choose navigation -> login -> desktop
```

## Boot-time input chooser
Right after the boot splash, GOSE shows **"How do you want to navigate?"**
(`gui/mockup/input-select.html`, concept `input-select-concept.png`). It adapts to
the platform and remembers the choice (changeable in Settings; auto-continues with
the default if untouched).

Model (shared logic in **`scripts/gose_input.py`**, web mirror
`gui/mockup/assets/platform.js`, tested in `agent/tests/test_input.py`):

| Platform | Default | Available | Peripherals |
|----------|---------|-----------|-------------|
| **Device** (Odin 2) | **Native** controls | Native · Controller · Keyboard | **Auto-accepted** — plug in a keyboard or pair a controller (BT/dongle/wired) anytime; it works alongside native |
| **PC app** | **Keyboard & mouse** | Keyboard · Controller | Choose Controller after connecting one (BT/USB); else falls back to keyboard |

- `resolve()` priority: explicit choice → remembered → platform default; PC +
  Controller with no pad connected → keyboard (never stranded).
- In the VM, the chosen controller is passed through to the guest
  (`--controller VID:PID`, USB passthrough); keyboard/mouse always available.
- Real peripheral enumeration per OS is **[needs hardware]**; the decision logic
  and UI are done and tested.
