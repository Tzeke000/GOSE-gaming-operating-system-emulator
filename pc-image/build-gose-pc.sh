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

# Pinned base image (override via env). [verify] against the current Batocera release.
BATOCERA_IMG_URL="${BATOCERA_IMG_URL:-https://mirrors.o2switch.fr/batocera/x86_64/stable/last/batocera-x86_64.img.gz}"
BATOCERA_SHA256="${BATOCERA_SHA256:-PUT_REAL_SHA256_HERE}"

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
    [[ "$BATOCERA_SHA256" != "PUT_REAL_SHA256_HERE" ]] || { echo "ERROR: set BATOCERA_SHA256 (and URL) to a real Batocera release."; exit 1; }
  fi
}

step "GOSE-PC build plan (dry-run=$DRY_RUN)"
echo "    base : $BATOCERA_IMG_URL"
echo "    layer: $LAYER"
echo "    out  : $OUT_IMG  +  $OUT_OVA  (${MEMORY_MB}MB / ${CPUS} vCPU / ${GROW_TO})"
require_real
run "mkdir -p '$WORK'"

step "1/6 Download + verify Batocera x86_64 base [needs network]"
run "curl -fL '$BATOCERA_IMG_URL' -o '$WORK/batocera.img.gz'"
run "echo '$BATOCERA_SHA256  $WORK/batocera.img.gz' | sha256sum -c -"

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
run "chmod +x '$WORK/mnt/system/custom.sh'"
run "umount '$WORK/mnt'"
run "losetup -d \$LOOP"

step "5/6 Package an importable OVA [needs qemu-img]"
run "python3 '$HERE/make_ova.py' --image '$OUT_IMG' --name GOSE-PC --memory '$MEMORY_MB' --cpus '$CPUS' --out '$OUT_OVA'"

step "6/6 Done"
echo "    Run it:    python3 $REPO/scripts/gose_vm.py --image '$OUT_IMG' --share ~/roms"
echo "    Or import: $OUT_OVA  (VirtualBox/VMware: File > Import Appliance)"
