#!/usr/bin/env bash
# Build the GOSE-PC image = Batocera x86_64 (base) + the GOSE layer, then package
# an importable .ova. Idempotent. Use --dry-run to print every step without
# touching the network/disk.
#
# Real build needs: network [downloads Batocera], root + loop mounts [injects the
# layer], and qemu-img [.ova]. Those steps are gated and clearly marked.
#
#   ./build-gose-pc.sh --dry-run        # show the plan
#   sudo ./build-gose-pc.sh             # real build (Linux host)
#
# Pin the base image via env (URL + sha256). Defaults are placeholders — set them
# to a real Batocera x86_64 release before a real build. See docs/11.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
WORK="${WORK:-$HERE/build}"
LAYER="$HERE/gose-layer"
OUT_IMG="$WORK/gose-pc-x86_64.img"
OUT_OVA="${OUT_OVA:-$HERE/GOSE-PC.ova}"

# Pinned base: Batocera 42 "Papilio Ulysses" (released 2025-10-12), x86_64 stable.
# Override via env. For a frozen, reproducible build set BATOCERA_IMG_URL to a
# versioned archive file (e.g. the Internet Archive "all versions" item) and
# BATOCERA_SHA256 to its published checksum.
BATOCERA_VERSION="${BATOCERA_VERSION:-42}"
BATOCERA_IMG_URL="${BATOCERA_IMG_URL:-https://mirrors.o2switch.fr/batocera/x86_64/stable/last/batocera-x86_64.img.gz}"
BATOCERA_SHA256="${BATOCERA_SHA256:-}"   # empty -> try sidecar .sha256, else warn

GROW_TO="${GROW_TO:-32G}"      # final virtual disk size
MEMORY_MB="${MEMORY_MB:-6144}"
CPUS="${CPUS:-4}"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

run() {  # echo in dry-run, execute otherwise
  if [[ $DRY_RUN -eq 1 ]]; then printf '  $ %s\n' "$*"; else eval "$@"; fi
}
step() { printf '\n==> %s\n' "$*"; }

require_real() {  # guard steps that can't be faked
  if [[ $DRY_RUN -eq 0 ]]; then
    [[ $EUID -eq 0 ]] || { echo "ERROR: real build needs root (loop mounts). Re-run with sudo."; exit 1; }
  fi
}

verify_base() {  # verify the downloaded base: pinned SHA, else sidecar, else warn
  local gz="$WORK/batocera.img.gz" sha="$BATOCERA_SHA256"
  if [[ -z "$sha" ]]; then
    if run "curl -fsL '${BATOCERA_IMG_URL}.sha256' -o '$WORK/base.sha256'"; then
      sha="$( [[ $DRY_RUN -eq 1 ]] && echo '<from-sidecar>' || awk '{print $1}' "$WORK/base.sha256" )"
    fi
  fi
  if [[ -n "$sha" && "$sha" != "<from-sidecar>" ]]; then
    run "echo '$sha  $gz' | sha256sum -c -"
  elif [[ "$sha" == "<from-sidecar>" ]]; then
    run "sha256sum -c '$WORK/base.sha256'"
  else
    echo "  ! no BATOCERA_SHA256 pinned and no sidecar checksum — proceeding UNVERIFIED"
    echo "    (set BATOCERA_SHA256 for a reproducible, verified build)"
  fi
}

step "GOSE-PC build plan (dry-run=$DRY_RUN)"
echo "    base : $BATOCERA_IMG_URL"
echo "    layer: $LAYER"
echo "    out  : $OUT_IMG  +  $OUT_OVA  (${MEMORY_MB}MB / ${CPUS} vCPU / ${GROW_TO})"
require_real
run "mkdir -p '$WORK'"

step "1/6 Download + verify Batocera $BATOCERA_VERSION x86_64 base [needs network]"
run "curl -fL '$BATOCERA_IMG_URL' -o '$WORK/batocera.img.gz'"
verify_base

step "2/6 Decompress to the working image"
run "gunzip -kf '$WORK/batocera.img.gz'"
run "mv -f '$WORK/batocera.img' '$OUT_IMG'"

step "3/6 Grow the image for headroom (ROMs/saves)"
run "truncate -s '$GROW_TO' '$OUT_IMG'"

step "4/6 Inject the GOSE layer into the userdata partition [needs root]"
run "LOOP=\$(losetup --show -fP '$OUT_IMG')"
run "mkdir -p '$WORK/mnt'"
# Batocera userdata is the last (share) partition; adjust index per release.
run "mount \${LOOP}p2 '$WORK/mnt'"
run "rsync -a '$LAYER/system/' '$WORK/mnt/system/'"
run "rsync -a '$LAYER/splash/' '$WORK/mnt/splash/'"
run "mkdir -p '$WORK/mnt/system/gose'"
run "rsync -a --exclude tests --exclude '__pycache__' '$REPO/agent' '$WORK/mnt/system/gose/'"
run "cat '$LAYER/system/batocera.conf.gose' >> '$WORK/mnt/system/batocera.conf'"
# Shell scripts are committed with CRLF (Windows authoring); /bin/bash + /bin/sh choke
# on the trailing \r ("word unexpected"), which would silently break the agent autostart
# and the first-boot provisioner/hardener. Normalize them before setting exec bits.
run "sed -i 's/\\r\$//' '$WORK/mnt/system/custom.sh' '$WORK/mnt/system/gose/provision-baked-apps.sh' '$WORK/mnt/system/gose/harden-firstboot.sh'"
run "chmod +x '$WORK/mnt/system/custom.sh'"
# Baked default app set (docs/25 §4): the manifest + first-boot provisioner ride in
# via the system/ rsync above; mark the provisioner executable (Windows checkouts
# carry no exec bit) so custom.sh's [ -x ] guard fires.
run "chmod +x '$WORK/mnt/system/gose/provision-baked-apps.sh'"
# Security hardener (Task #83) ships alongside the provisioner; same exec-bit fixup.
run "chmod +x '$WORK/mnt/system/gose/harden-firstboot.sh'"

step "4b/6 Bake the GOSE shell (product UI + server) into /userdata/gose-ui [Task #90]"
# THE ship-blocker fix: the build previously baked ONLY system/ + agent/, so a clean
# image booted hardened Batocera with NO GOSE UI and packaging fell back to the dev disk.
# Bake the shell from its CANONICAL repo sources (build-time COPY, never duplicated into
# gose-layer, so it can't drift): gui/mockup (the kiosk pages + assets) + pc-image/
# gose-vm-host (the UI server + shell helpers + vendored python-xlib). Mirrors the live
# dev VM's /userdata/gose-ui inventory exactly (verified file-for-file, docs/32).
UI="$WORK/mnt/gose-ui"
HOST="$REPO/pc-image/gose-vm-host"
run "mkdir -p '$UI'"
# (1) kiosk pages + assets + page-render helpers; drop design concepts, caches, backups.
run "rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '*-concept.png' --exclude '*.bak' '$REPO/gui/mockup/' '$UI/'"
# (2) UI server + shell helpers — the guest-runtime subset of gose-vm-host. Host-only dev
#     tooling (reload_ui.py / push_*.py / boot-gose-vm.ps1 / host_bridge.py / inject_gose_layer.py
#     / swap_shell.py / serve_and_kiosk.py / elev*) is intentionally NOT baked into the guest.
for f in gose_vm_server.py kiosk.py gose-session.sh gose-pad-nav.py overlay_window.py \
         watchdog.py gose-storage-handler.sh guide_toggle.sh shot.sh start-shell.sh \
         99-gose-storage.rules gamecontrollerdb.txt; do
  run "rsync -a '$HOST/$f' '$UI/'"
done
# (3) vendored python-xlib (the overlay/cursor code imports it) — whole tree minus caches.
run "rsync -a --exclude '__pycache__' --exclude '*.pyc' '$HOST/vendor' '$UI/'"
# Normalize CRLF on the baked shell scripts (python tolerates CRLF; /bin/sh does not) and
# set the exec bits the Windows checkout drops (guide_toggle.sh / shot.sh run via Openbox).
run "find '$UI' -maxdepth 1 -name '*.sh' -exec sed -i 's/\\r\$//' {} +"
run "chmod +x '$UI'/*.sh"
# The shell writes pad-nav/overlay logs to /userdata/system/logs at S31 — before custom.sh
# creates it at S99 — so pre-create it or first-boot pad navigation silently fails.
run "mkdir -p '$WORK/mnt/system/logs'"

run "umount '$WORK/mnt'"

step "4c/6 Install the pre-ES shell autostart hook on the BOOT partition"
# How the shell AUTOSTARTS: Batocera launches the front-end via /usr/bin/emulationstation-
# standalone; GOSE swaps that script's ES launch line for `sh /userdata/gose-ui/gose-session.sh`.
# On the dev disk that edit lived in the Batocera overlay; a clean build has none, so we
# (re)apply it each boot from /boot/boot-custom.sh, which S00bootcustom runs BEFORE
# S31emulationstation. Idempotent + self-heals after an OS update. (boot = FAT partition p1.)
run "mount \${LOOP}p1 '$WORK/mnt'"
run "rsync -a '$LAYER/boot/boot-custom.sh' '$WORK/mnt/boot-custom.sh'"
run "sed -i 's/\\r\$//' '$WORK/mnt/boot-custom.sh'"
run "umount '$WORK/mnt'"
run "losetup -d \$LOOP"

step "5/6 Package an importable OVA [needs qemu-img]"
run "python3 '$HERE/make_ova.py' --image '$OUT_IMG' --name GOSE-PC --memory '$MEMORY_MB' --cpus '$CPUS' --out '$OUT_OVA'"

step "6/6 Done"
echo "    Run it:    python3 $REPO/scripts/gose_vm.py --image '$OUT_IMG' --share ~/roms"
echo "    Or import: $OUT_OVA  (VirtualBox/VMware: File > Import Appliance)"
