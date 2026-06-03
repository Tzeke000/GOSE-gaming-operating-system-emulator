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
