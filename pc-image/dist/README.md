# GOSE — download & double-click

This is the **distributable GOSE bundle**: the "I downloaded GOSE from Steam/GitHub
and it just runs" experience. GOSE boots inside **its own virtual machine** — installing
or running it **never converts your real Windows machine** and needs **no admin** to launch.

## Start GOSE
**Double-click `GOSE.bat`** (or the `GOSE` desktop shortcut). That's it:
- First run: provisions the GOSE disk (decompresses `vm\gose-disk.img.gz` once), then boots.
- Already running: it just brings the GOSE window to the front (no second VM).
- You land in GOSE — first boot shows the setup wizard (OOBE); after that, the desktop.

To put a desktop icon there:
```
powershell -NoProfile -ExecutionPolicy Bypass -File make-shortcut.ps1
```

## What's in the bundle
```
GOSE\
├── GOSE.bat                    ← double-click entry point
├── make-shortcut.ps1           ← creates the "GOSE" desktop shortcut (.lnk) with the icon
├── README.md                   ← this file
├── launcher\
│   ├── gose-launcher.ps1       ← orchestrator: provision → boot → focus the GOSE window
│   ├── boot-gose-vm.ps1        ← REUSED working boot (virgl GPU + audio + Bluetooth + host bridge)
│   ├── host_bridge.py          ← feeds real battery/network state into the VM
│   └── gose.ico                ← GOSE Core brand icon (from gui/mockup/assets/brand)
├── qemu\                       ← portable QEMU (mingw64 bin: qemu-system-x86_64.exe + DLLs)
└── vm\
    └── gose-disk.img(.gz)      ← the GOSE OS disk image (ships compressed; provisioned on first run)
```

The launcher resolves these **bundle-relative** paths first and falls back to the dev box,
so the same script works for a developer (`D:\gose-vm`, `D:\gose-build\msys64`) and for a
downloaded copy. It points the reused `boot-gose-vm.ps1` at the bundle's QEMU + image via
the `GOSE_QEMU_BIN` / `GOSE_IMAGE` / `GOSE_BRIDGE` env overrides (defaults unchanged).

## Sandboxing / your machine is safe
GOSE runs entirely inside the QEMU VM. The launcher does not install drivers, change the
registry, or modify your OS. The only host touch points are: a loopback-only port (the
in-VM agent on `127.0.0.1:8731`, token-authenticated) and optional, user-initiated
peripheral passthrough. Remote access (if enabled) goes over Tailscale, never raw LAN.

## One-time host prerequisite (honest)
QEMU uses **WHPX** (Windows Hypervisor Platform) for acceleration. On most machines this
is available; if not, enabling it is a **one-time** Windows step (Settings → "Turn Windows
features on or off" → *Windows Hypervisor Platform*, reboot) — that toggle needs admin, but
**launching GOSE afterward does not**. This is the single elevation point and it is isolated
to first-time host setup, not the app itself.

## What this is vs. what's still TODO
See `../../docs/17-os-roadmap.md` §E and the "Distribution launcher" section for the honest
split: this is a **working local launcher**. Real **Steam depot packaging, code-signing, and
auto-update are still TODO**. The portable `qemu\` and `vm\` payloads are not committed to git
(too large / built artifacts) — they are assembled at package time; this folder is the layout
+ scripts that turn the built image into the double-click product.
