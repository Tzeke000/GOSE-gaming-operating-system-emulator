# Controller Support Matrix & Setup

Target: speak Xbox, PlayStation, Nintendo, and 8BitDo natively/auto-mapped; up to
~6 players (6 = docked-to-TV scenario; pure Bluetooth caps ~3–4).

| Controller | BT | USB | Driver / notes | Difficulty |
|-----------|----|-----|----------------|-----------|
| Xbox One / Series | ✅ | ✅ | **`xpadneo`** = full support incl. rumble | easy |
| PS4 DualShock 4 | ✅ | ✅ | native (`hid-sony`/`hid-playstation`) | easy |
| PS5 DualSense | ✅ | ✅ | native (`hid-playstation`), touchpad/rumble | easy |
| Switch Pro | ✅ | ✅ | native `hid-nintendo`, BT pairing fussy | medium |
| Joy-Cons | ✅ | — | `hid-nintendo`; pairing/mapping tweaks | hard |
| 8BitDo (various) | ✅ | ✅ | native; has Pi/Switch/PC mode switch | easy |
| **Any, via 8BitDo USB Adapter 2** | — | ✅ | universal translator, 1 controller/dongle | easy |

## Multi-controller plan
- **Handheld / portable:** Bluetooth (3–4) + USB-C OTG hub (PD pass-through, 2+
  USB-A) for extra dongles.
- **Docked / TV (6 players):** AYN Super Dock USB ports for the extra pads/dongles;
  HDMI out (fall back to USB-C→HDMI adapter if dock HDMI fails on Linux).

## Setup tasks (mostly handled by the distro; document specifics on hardware)
- [ ] Confirm `xpadneo` present/loaded on the chosen distro (ROCKNIX/Batocera
      usually bundle it). `[needs hardware]`
- [ ] Pair + verify each pad; capture per-pad mapping quirks here as we hit them.
- [ ] Test the 8BitDo USB Adapter 2 as the universal fallback.
- [ ] Validate 6 pads docked.

## Note on the AI as a "virtual controller"
The GOSE Agent creates a **virtual gamepad via `uinput`** (see agent docs) that
emulators see as just another pad — so the AI shares the exact same input path as
physical controllers. No special emulator support needed.

## Input architecture — driving the OS with pad + mouse/keyboard + PS5
The Windows-style desktop and the games must be navigable three ways (the owner's
requirement). All ride on standard Linux input (`evdev`); the front-end and
emulators read them via SDL2.

| Source | OS navigation | In-game |
|--------|---------------|---------|
| **Native Odin 2 pad** | D-pad/stick move highlight (focus-nav); also can drive an on-screen **pointer** | full control |
| **USB/BT mouse + keyboard** | native pointer + typing (setup/tinkering) | as the game supports |
| **PS5 DualSense** (BT/USB) | same as native pad (focus-nav or pointer) | native `hid-playstation` |

**The Windows-desktop pointer problem:** a real desktop expects a mouse. Two
complementary solutions, both controller-only:
1. **Focus-nav** (built into our front-end): D-pad/stick moves a highlight, A
   selects, B backs — no pointer needed. The default; fastest for tiles/lists.
2. **Pointer mode** via **AntiMicroX** (`docs/09-toolchain.md`): maps the right
   stick to mouse movement and buttons to clicks/keys, with **per-app
   auto-profiles** (e.g. pointer inside a file manager, focus-nav on the home
   screen). This makes *any* desktop app usable from the pad.

The HTML prototype (`gui/mockup/desktop.html`) already demonstrates both: **Y**
toggles focus-nav ↔ pointer; works with keyboard, mouse, and any standard gamepad
(Odin pad, Xbox, PS5) via the browser Gamepad API.

## On-screen keyboard
For the keyboard-optional rule, a controller-driven on-screen keyboard handles
text entry (Wi-Fi password, search). The front-ends ship one; the custom desktop
will surface it on any text field. A real USB/BT keyboard remains optional for
tinkering.
