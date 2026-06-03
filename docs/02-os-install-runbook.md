# OS Install Runbook (reproducible) — [needs hardware]

Goal: from a stock Android Odin 2 to a booting Linux gaming OS on SD, **without
wiping Android**. Everything here runs on the Odin/your tower, not in the cloud
container, so it is marked **[needs hardware]**. Keep this file the single source
of truth so a re-flash never means rebuilding from memory.

> Safety: the bootloader (abl) step modifies boot. Back it up first (the scripts do
> this). You can always switch boot mode back to Android. Don't skip the backup.

## A. Pick the distro
- **ROCKNIX (recommended first):** stable on Odin 2 today, microSD-only, Android
  untouched.
- **Batocera v42 (SM8550 image):** put on a *second* SD card to compare emulation
  coverage. Both can coexist on separate cards.

## B. Materials
- Fast **A2 microSD 256GB+** (plus a spare card for experiments).
- microSD reader on your Windows tower.
- Imager: Balena Etcher (cross-platform) / Rufus (Windows) / `dd` (Linux).
- The Odin 2 with a charged battery; USB-C cable.

## C. Flash the SD (on the tower)
1. Download the image:
   - ROCKNIX: latest Odin 2 image from https://rocknix.org/devices/ayn/odin2/ (or
     GitHub releases).
   - Batocera: the **SM8550** image from batocera.org.
2. Write it to the microSD with Etcher/Rufus (or `dd if=image of=/dev/sdX bs=4M
   status=progress conv=fsync` — triple-check `/dev/sdX`).
3. Insert the card into the Odin 2.

## D. One-time bootloader (abl) modification — ROCKNIX flow
1. With the ROCKNIX card inserted and booted into Android, copy the **`rocknix_abl`**
   folder from the SD to the **root of Internal Storage**.
2. In a terminal/ADB shell on the device, run:
   - `sh backup_abl.sh`  ← backs up your current abl (KEEP this backup).
   - `sh flash_abl.sh`   ← installs the boot-mode switch.
3. Reboot **holding Vol−** to enter the fastboot menu.
4. Use **Vol+/−** to highlight **"Switch boot mode"**, press **Power** to validate,
   then press **Power** again to start ROCKNIX from SD.

(Batocera's flow is analogous — follow the batocera wiki SM8550 instructions; the
boot-mode-switch concept is the same.)

## E. Return to Android
- Reboot, enter fastboot (Vol−), **Switch boot mode** back to Android. Internal
  Android is untouched throughout.

## F. First-boot checklist (validate on hardware, tick in ROADMAP)
- [ ] Boots to the front-end; **native Odin buttons/sticks navigate** (no keyboard).
- [ ] Wi-Fi connects (on-screen keyboard for password).
- [ ] Bluetooth pairs a controller.
- [ ] GPU accel active (Vulkan/Turnip) — check a 3D core runs smoothly.
- [ ] Audio out of built-in speakers.
- [ ] microSD games visible.

## G. TV-out (when docking) — [needs hardware]
- Try the AYN Super Dock HDMI first. **If HDMI is black/no-signal, switch to a
  direct USB-C→HDMI adapter** (known Linux dock-HDMI driver gap). Dock USB +
  Ethernet should work either way.

## H. Reproducibility
After the OS boots, run `scripts/setup-device.sh` (over SSH) to apply all GOSE
customizations idempotently: install the GOSE Agent, drop emulator default
configs, install the Windows-like theme, enable SSH, etc. The goal: **re-flash →
run one script → fully restored.** Keep that script honest as we add customizations.
