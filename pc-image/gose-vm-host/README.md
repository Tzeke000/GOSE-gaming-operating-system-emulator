# GOSE VM host + guest operational scripts (backup)

Live copies run on the Windows HOST at `D:\gose-vm\` and inside the guest at `/userdata/gose-ui/`.
Pushed here for backup/version history. Excludes the Batocera .img and the USBDk .msi (large binaries).

- Host: boot-gose-vm.ps1, host_bridge.py, capture.ps1, elev_agent.ps1, elev_launch.bat
- Guest (/userdata/gose-ui): gose_vm_server.py, kiosk.py, overlay_window.py, watchdog.py, gose-session.sh
