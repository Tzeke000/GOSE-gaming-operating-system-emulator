# Research Findings (verified 2026-06-03)

Sources are linked at the bottom. Re-verify version numbers before flashing — this
space moves fast.

## 1. Does Linux run on the Odin 2 yet? YES — both distros support it now.

### ROCKNIX — officially stable on all three Odin 2 variants ✅
- Official builds support **Odin 2, Odin 2 Mini, and Odin 2 Portal** (graduated
  from alpha/beta to official; the 2025-05-17 build added all three).
- Runs entirely off microSD, **leaves Android untouched** → safe dual-boot.
- Now even ships **Steam support** for select Android handhelds incl. Odin 2.
- **→ Recommended base distro for "works reliably today."**

### Batocera — v42 adds Odin 2 via the SM8550 image ✅
- Batocera v42 brings Odin 2 / Odin 2 Portal compatibility (Snapdragon 8 Gen 2 /
  SM8550). Early support includes proper button mapping, **Vulkan via
  Freedreno/Turnip**, working Wi-Fi + Bluetooth.
- Use the **Batocera SM8550 image**. Was first available in the "Butterfly" dev
  branch; verify the current stable v42 build supports the exact variant.
- Biggest emulation library + polished. Linux unlocks heavier cores (PS3, etc.).

### Why Linux now: AYN open-sourced a mainline kernel
AYN published mainline Linux kernel source for the Odin 2 on GitHub, which let both
Batocera and ROCKNIX port to it. The Odin 2 is among the most powerful ARM
handhelds with Linux support.

## 2. Install mechanics (both distros, summarized — full steps in runbook)
- One-time **bootloader (abl) modification**: copy an `rocknix_abl` (or distro
  equivalent) folder to internal storage, run `backup_abl.sh` then `flash_abl.sh`.
- Boot Linux: reboot holding **Vol−** to reach fastboot menu → use Vol+/− to select
  **"Switch boot mode"** → Power to confirm → Power again to boot the SD OS.
- Returning to Android = switch boot mode back. Android stays on internal storage.
- Burn the distro image to a **fast A2 microSD** with Balena Etcher / Rufus / `dd`.

## 3. Front-end reality check (correction to original brief)
- **Batocera uses its OWN fork: `batocera-emulationstation`** (XML theme format
  **version 7**). It is **NOT** ES-DE (EmulationStation Desktop Edition).
- ES-DE is a separate project (RetroDECK/standalone) with an incompatible theme
  engine; Batocera/Recalbox themes must be *ported* to run on ES-DE and vice-versa.
- **ROCKNIX** also uses an EmulationStation fork with its own theme support.
- **Implication for the Windows-like GUI:** a theme approach targets the
  `batocera-emulationstation` (or ROCKNIX ES) XML theme engine. A custom front-end
  app sidesteps the theme engine entirely. See `06-gui-plan.md`.

## 4. Controllers (consistent with brief; kernel handles most)
- **Xbox One/Series:** `xpadneo` driver = full support incl. rumble (BT + USB).
- **PS4 DualShock 4 / PS5 DualSense:** native Linux support, smooth (BT + USB).
- **Switch Pro / Joy-Cons:** work over BT but fussiest; expect mapping tweaks.
- **8BitDo:** native; has Pi/Switch/PC mode switch; USB Wireless Adapter 2 acts as
  a universal one-controller-per-dongle translator.
- **6-player = docked scenario** (dock USB ports); pure Bluetooth caps ~3–4.

## 5. Known hardware caveats confirmed
- **Dock HDMI on Linux can fail** (missing/immature kernel drivers on some
  AYN/Retroid docks) while a **plain USB-C→HDMI adapter works**. Design TV-out to
  fall back to the direct adapter. Dock USB + Ethernet are more reliable.
- Newest SoC = least mature drivers; re-verify GPU/Wi-Fi/dock for the exact build.
- Single native USB-C shares charging + OTG; simultaneous OTG+charge depends on
  device support — **[needs hardware] to confirm on Odin 2.**

## Sources
- Batocera v42 / Odin 2: https://metalgamesolid.com/emulation/emu-devices/batocera-v42-odin-2-compatibility-and-big-improvements-coming-soon/
- AYN Linux kernel → Batocera/ROCKNIX: https://www.androidauthority.com/ayn-odin-2-linux-support-3529916/
- "Your Odin 2 might soon run Batocera": https://retrohandhelds.gg/your-odin-2-might-soon-be-able-to-run-batocera/
- ROCKNIX Odin 2 device wiki: https://rocknix.org/devices/ayn/odin2/
- ROCKNIX official build adds Odin 2 (all three): https://retrohandhelds.gg/rocknix-officially-arrives-for-rg34xxsp-retroid-pocket-flip-2-odin-2-portal-and-others/
- Retro Game Corps ROCKNIX-on-Odin-2 guide: https://retrogamecorps.com/2025/03/03/linux-on-the-odin-2-rocknix-guide/
- Batocera AYN hardware wiki: https://wiki.batocera.org/hardware:ayn
- Batocera ES themes (format v7): https://github.com/batocera-linux/batocera-emulationstation/blob/master/THEMES.md
- ES-DE vs Batocera theme incompatibility: https://es-de.org/ and https://gitlab.com/es-de/emulationstation-de/-/blob/master/FAQ.md
