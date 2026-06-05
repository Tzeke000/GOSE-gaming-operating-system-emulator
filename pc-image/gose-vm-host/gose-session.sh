#!/bin/sh
# GOSE shell session — REPLACES EmulationStation as what the OS launches.
# Called in place of the `emulationstation` binary inside emulationstation-standalone's
# display loop (so the display is already configured + the loop relaunches us = watchdog).
pgrep -f gose_vm_server >/dev/null 2>&1 || \
  (cd /userdata/gose-ui && nohup python3 -u gose_vm_server.py >>/userdata/gose-ui/shell.log 2>&1 &)
# no screen blanking / screensaver — GOSE is the shell; nothing should dim or show a clock overlay
xset -display :0 s off s noblank -dpms 2>/dev/null
# NumLock ON so the numpad acts as a controller everywhere (idempotent across relaunches)
NL=$(DISPLAY=:0 xset q 2>/dev/null | sed -n 's/.*Num Lock:[[:space:]]*\([a-z]*\).*/\1/p')
[ "$NL" = "on" ] || DISPLAY=:0 xdotool key Num_Lock 2>/dev/null
# Guide overlay: a persistent always-on-top panel that can appear OVER a running game.
pgrep -f overlay_window.py >/dev/null 2>&1 || \
  (cd /userdata/gose-ui && DISPLAY=:0 nohup python3 -u overlay_window.py >>/userdata/gose-ui/overlay.log 2>&1 &)
# Watchdog: auto-restart the UI server / overlay if they die mid-session (availability/recovery).
pgrep -f watchdog.py >/dev/null 2>&1 || \
  (cd /userdata/gose-ui && nohup python3 -u watchdog.py >>/userdata/gose-ui/gose.log 2>&1 &)
# Bind the Guide (numpad 5 / Home) globally in Openbox so it toggles the overlay even over a game.
if ! grep -q guide_toggle /etc/openbox/rc.xml 2>/dev/null; then
  sed -i 's#</openbox_config>#  <keyboard>\n    <keybind key="KP_5"><action name="Execute"><command>/userdata/gose-ui/guide_toggle.sh</command></action></keybind>\n    <keybind key="KP_Begin"><action name="Execute"><command>/userdata/gose-ui/guide_toggle.sh</command></action></keybind>\n    <keybind key="Print"><action name="Execute"><command>/userdata/gose-ui/shot.sh</command></action></keybind>\n  </keyboard>\n</openbox_config>#' /etc/openbox/rc.xml
  DISPLAY=:0 openbox --reconfigure 2>/dev/null
fi
# Boot splash on the FIRST kiosk launch per VM boot (/tmp clears on reboot); the desktop on relaunches.
if [ ! -f /tmp/gose-booted ]; then
  touch /tmp/gose-booted
  exec python3 /userdata/gose-ui/kiosk.py http://127.0.0.1:8780/gose-boot.html
else
  exec python3 /userdata/gose-ui/kiosk.py http://127.0.0.1:8780/gose-home.html
fi
