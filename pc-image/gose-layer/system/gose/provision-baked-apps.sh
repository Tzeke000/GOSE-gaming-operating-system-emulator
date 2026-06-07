#!/bin/sh
# GOSE first-boot app provisioner — guarantees the docs/25 §4 "baked-in" default
# app set is installed, and Firefox is the default browser. Idempotent: only the
# MISSING ids from baked-apps.list are installed, so a normal boot is a no-op and a
# factory reset (which wipes /userdata flatpaks) re-lands the full set.
#
# Called detached from custom.sh `start`. Logs to /userdata/system/logs.
GOSE="$(cd "$(dirname "$0")" && pwd)"
LIST="$GOSE/baked-apps.list"
LOG=/userdata/system/logs/gose-provision-apps.log
mkdir -p "$(dirname "$LOG")"
echo "=== provision-baked-apps $(date) ===" >>"$LOG"

command -v flatpak >/dev/null 2>&1 || { echo "no flatpak — skip" >>"$LOG"; exit 0; }
# Flathub must exist for installs (no-op if already added).
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo >>"$LOG" 2>&1 || true

installed="$(flatpak list --app --columns=application 2>/dev/null)"
[ -f "$LIST" ] || { echo "no baked-apps.list — skip" >>"$LOG"; exit 0; }

while IFS= read -r raw; do
  id="$(echo "$raw" | sed 's/#.*//' | tr -d '[:space:]')"
  [ -z "$id" ] && continue
  if echo "$installed" | grep -qx "$id"; then
    echo "present: $id" >>"$LOG"; continue
  fi
  echo "installing: $id" >>"$LOG"
  n=0
  while [ "$n" -lt 5 ]; do
    if flatpak install -y --noninteractive flathub "$id" >>"$LOG" 2>&1; then
      echo "installed: $id" >>"$LOG"; break
    fi
    n=$((n+1)); echo "retry $n: $id" >>"$LOG"; sleep 8   # ride transient network errors
  done
done < "$LIST"

# Firefox = default browser (Batocera has no xdg-settings; write mimeapps.list directly).
# Persistent under HOME on /userdata. Only (re)write if Firefox is actually installed.
if echo "$(flatpak list --app --columns=application 2>/dev/null)" | grep -qx org.mozilla.firefox; then
  cfg="${HOME:-/userdata/system}/.config"
  mkdir -p "$cfg"
  if ! grep -q '^x-scheme-handler/https=org.mozilla.firefox.desktop' "$cfg/mimeapps.list" 2>/dev/null; then
    cat > "$cfg/mimeapps.list" <<EOF
[Default Applications]
x-scheme-handler/http=org.mozilla.firefox.desktop
x-scheme-handler/https=org.mozilla.firefox.desktop
x-scheme-handler/about=org.mozilla.firefox.desktop
x-scheme-handler/unknown=org.mozilla.firefox.desktop
text/html=org.mozilla.firefox.desktop
application/xhtml+xml=org.mozilla.firefox.desktop
EOF
    echo "set default browser: firefox" >>"$LOG"
  fi
fi
echo "=== done $(date) ===" >>"$LOG"
