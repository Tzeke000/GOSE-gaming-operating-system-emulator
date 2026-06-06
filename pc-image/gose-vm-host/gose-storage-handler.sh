#!/bin/sh
# GOSE storage auto-import udev handler (docs/25 §5.3).
# Fired by 99-gose-storage.rules IN PARALLEL with Batocera's own storage stack. Batocera's
# batocera-storage-manager already MOUNTS the inserted volume under /media/<label> (and skips the
# system/boot/userdata LUNs); we do NOT re-implement any of that. We only wait for that mount to
# appear, then tell the GOSE server to scan + offer. Detaches immediately (setsid) so udev's RUN+=
# returns at once and the worker survives past the udev event.
ACTION="$1"; DEV="$2"
LOG=/userdata/gose-ui/storage-handler.log
SRV=http://127.0.0.1:8780

[ -z "$ACTION" ] && exit 0
[ -z "$DEV" ] && exit 0

# Re-exec the real work fully detached unless we already are the detached worker.
if [ -z "$GOSE_SH_WORKER" ]; then
  GOSE_SH_WORKER=1 setsid "$0" "$ACTION" "$DEV" >>"$LOG" 2>&1 </dev/null &
  exit 0
fi

post() { curl -s -m 8 -X POST -H "Content-Type: application/json" -d "$2" "$SRV$1"; echo; }
echo "[$(date '+%F %T')] event: $ACTION $DEV"

if [ "$ACTION" = "remove" ]; then
  post /storage/removed "{\"dev\":\"$DEV\"}"
  exit 0
fi

# add: wait (<=30s) for Batocera's storage-manager to mount this partition under /media/<name>.
MP=""
i=0
while [ "$i" -lt 30 ]; do
  MP=$(awk -v d="/dev/$DEV" '$1==d {print $2; exit}' /proc/mounts)
  case "$MP" in
    /media/*) break ;;
    *) MP="" ;;
  esac
  i=$((i + 1)); sleep 1
done
[ -z "$MP" ] && { echo "  no /media mount for $DEV after 30s -- skipping (not a ROM volume)"; exit 0; }

# strip any quote/backslash from the label so it can't break the JSON body
LABEL=$(blkid -s LABEL -o value "/dev/$DEV" 2>/dev/null | tr -d '"\\')
VID=$(blkid -s UUID -o value "/dev/$DEV" 2>/dev/null | tr -d '"\\')
[ -z "$VID" ] && VID="$DEV"
echo "  mounted at $MP (label='$LABEL' uuid='$VID') -> POST /storage/detected"
post /storage/detected "{\"dev\":\"$DEV\",\"mount\":\"$MP\",\"label\":\"$LABEL\",\"vol_id\":\"$VID\"}"
