#!/bin/sh
# GOSE pre-ES boot hook.
#
# Installed by build-gose-pc.sh to the ROOT of the Batocera FAT boot partition as
# /boot-custom.sh. Batocera's S00bootcustom init runs `bash /boot/boot-custom.sh $1`
# BEFORE S31emulationstation starts the front-end, which is exactly the window we
# need to redirect the front-end to the GOSE shell.
#
# WHY THIS EXISTS (Task #90): the GOSE shell is launched by REPLACING the stock
# EmulationStation launch line inside /usr/bin/emulationstation-standalone with the
# GOSE session script. On the hand-built dev disk that edit was persisted in the
# Batocera *overlay* (/boot/boot/overlay). A CLEAN build from a stock base has no
# such overlay, so the patch is gone and the image boots into stock Batocera ES with
# no GOSE UI. We (re)apply the patch here on every boot instead of baking the overlay:
#   - the rootfs is a writable overlay at runtime, so the edit takes effect for THIS
#     boot's ES (S00 < S31);
#   - it is idempotent (no-op once patched) and version-independent;
#   - it SELF-HEALS after a Batocera OS update, which restores the stock squashfs.
#
# Batocera passes "start"/"stop"; only act on start (and on a bare invocation).
[ "$1" = "stop" ] && exit 0

ESS=/usr/bin/emulationstation-standalone
SESSION=/userdata/gose-ui/gose-session.sh

# Only patch once the GOSE shell is actually present on /userdata, and only if the ES
# launcher has not already been redirected (idempotent across reboots).
if [ -f "$ESS" ] && [ -f "$SESSION" ] && ! grep -q 'gose-ui/gose-session.sh' "$ESS"; then
  # Swap the stock "dbus-run-session -- emulationstation <opts>" launch line for the
  # GOSE session, preserving the dbus-run-session wrapper the front-end needs. This is
  # byte-for-byte the line the working dev disk runs (verified against the live VM).
  sed -i 's#dbus-run-session -- emulationstation .*#dbus-run-session -- sh /userdata/gose-ui/gose-session.sh#' "$ESS"
fi
exit 0
