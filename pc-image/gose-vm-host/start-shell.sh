#!/bin/sh
# GOSE shell autostart: serve the UI (+ live dials from the loopback agent) and
# launch the WebKit kiosk fullscreen once X is up. Called from custom_service at boot.
LOG=/userdata/gose-ui/shell.log
echo "=== gose-shell start $(date) ===" >> "$LOG"
# UI + live-telemetry server
pkill -f gose_vm_server 2>/dev/null
nohup python3 -u /userdata/gose-ui/gose_vm_server.py >> "$LOG" 2>&1 &
# wait for the X server on :0 (started by Batocera's startx)
i=0
while [ $i -lt 90 ]; do
  [ -e /tmp/.X11-unix/X0 ] && break
  i=$((i+1)); sleep 2
done
sleep 6   # let X + ES settle
pkill -f kiosk.py 2>/dev/null
# WATCHDOG: keep the GOSE shell up. If it ever exits (crash, game return, focus loss),
# kill any leftover game and relaunch — so it never reverts to the Batocera menu.
echo "gose-shell: entering watchdog" >> "$LOG"
while true; do
  pkill -f emulatorlauncher 2>/dev/null
  DISPLAY=:0 python3 /userdata/gose-ui/kiosk.py \
    http://127.0.0.1:8780/gose-home.html >> "$LOG" 2>&1
  sleep 2
done
