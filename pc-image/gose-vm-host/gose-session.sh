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
# Gamepad -> keyboard bridge: WebKit has no gamepad lib + no evmapy/gptokeyb here,
# so the controller can't drive the (keyboard-navigable) UI on its own. This daemon
# reads the pad via evdev and synthesizes the matching X keys (xdotool). Idempotent:
# kill any stale instance first so a session relaunch never stacks duplicates.
pkill -f gose-pad-nav.py 2>/dev/null
( cd /userdata/gose-ui && DISPLAY=:0 setsid python3 -u gose-pad-nav.py \
    >>/userdata/system/logs/gose-pad-nav.log 2>&1 </dev/null & )
# Storage auto-import (docs/25 §5.3): install the GOSE udev rule so an inserted SD/USB with ROMs is
# detected and offered to the Library. Runs ALONGSIDE Batocera's storage rule (reuses its mount).
# /etc/udev/rules.d is a non-persistent overlay, so (re)install + reload on every boot. Idempotent.
chmod +x /userdata/gose-ui/gose-storage-handler.sh 2>/dev/null
if [ -f /userdata/gose-ui/99-gose-storage.rules ]; then
  if ! cmp -s /userdata/gose-ui/99-gose-storage.rules /etc/udev/rules.d/99-gose-storage.rules 2>/dev/null; then
    cp /userdata/gose-ui/99-gose-storage.rules /etc/udev/rules.d/99-gose-storage.rules
    udevadm control --reload 2>/dev/null
  fi
fi
# Bind the Guide (numpad 5 / Home) globally in Openbox so it toggles the overlay even over a game.
if ! grep -q guide_toggle /etc/openbox/rc.xml 2>/dev/null; then
  sed -i 's#</openbox_config>#  <keyboard>\n    <keybind key="KP_5"><action name="Execute"><command>/userdata/gose-ui/guide_toggle.sh</command></action></keybind>\n    <keybind key="KP_Begin"><action name="Execute"><command>/userdata/gose-ui/guide_toggle.sh</command></action></keybind>\n    <keybind key="Print"><action name="Execute"><command>/userdata/gose-ui/shot.sh</command></action></keybind>\n  </keyboard>\n</openbox_config>#' /etc/openbox/rc.xml
  DISPLAY=:0 openbox --reconfigure 2>/dev/null
fi
# First-boot routing (docs/25): a fresh install (no .oobe-done flag) lands on the OOBE wizard;
# once setup is finished the flag exists and we go to the desktop. This also covers a watchdog
# RELAUNCH mid-setup -> back to the wizard, not the desktop. Reset = remove the flag (or factory
# reset) to re-run the wizard. The boot splash itself re-checks the flag via /oobe/status.
if [ -f /userdata/system/gose/.oobe-done ]; then
  LAND=gose-home.html
else
  LAND=gose-oobe.html
fi
# Boot splash on the FIRST kiosk launch per VM boot (/tmp clears on reboot); the landing page on relaunches.
if [ ! -f /tmp/gose-booted ]; then
  touch /tmp/gose-booted
  exec python3 /userdata/gose-ui/kiosk.py http://127.0.0.1:8780/gose-boot.html
else
  exec python3 /userdata/gose-ui/kiosk.py "http://127.0.0.1:8780/$LAND"
fi
