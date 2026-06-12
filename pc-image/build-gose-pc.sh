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

# --- VERSION: single source of truth ------------------------------------------
# The VERSION file at the repo root is the canonical version for both the build
# and the running OS. Read it once here; the integrator must also update the
# VERSION constant in gose_vm_server.py to match (see docs/18 §SB-1.3).
GOSE_VERSION="$(cat "$REPO/VERSION" 2>/dev/null || echo "unknown")"

# Pinned base: Batocera 43.1 (reconciled with gose_vm_server.py VERSION, SB-1.3).
# Override via env. For a frozen, reproducible build set BATOCERA_IMG_URL to a
# versioned archive file (e.g. the Internet Archive "all versions" item) and
# BATOCERA_SHA256 to its published checksum.
BATOCERA_VERSION="${BATOCERA_VERSION:-43.1}"
BATOCERA_IMG_URL="${BATOCERA_IMG_URL:-https://mirrors.o2switch.fr/batocera/x86_64/stable/43.1/batocera-x86_64-43.img.gz}"
BATOCERA_SHA256="${BATOCERA_SHA256:-}"   # empty -> try sidecar .sha256, else warn

# --- License / distribution mode ----------------------------------------------
# SHIP_SAFE=1 (default): strip non-commercial libretro cores and trademark-laden
#   ROMs. Required for any paid/public distribution (Steam, GitHub Releases, etc.).
#   See docs/19-license-audit.md §1 and §5 for the full rationale.
# SHIP_SAFE=0: keep all stock Batocera cores. Use ONLY for local dev / testing on
#   a private machine. NEVER distribute a SHIP_SAFE=0 build commercially.
SHIP_SAFE="${SHIP_SAFE:-1}"

# Libretro cores that carry a non-commercial license and MUST be removed from any
# commercial/public distribution (docs/19 §1 and §5A). These are the 11 EXCLUDE
# entries from the full 117-core inventory audit (2026-06-06, verified against
# upstream LICENSE files). Removing them does not drop whole systems because
# commercial-OK alternatives exist on the same image (docs/19 §5B).
#
# REVIEW cores (picodrive, hatarib, zc210) are also excluded here pending legal
# review — they will join the OK set only after upstream license confirmation
# (docs/19 §3 and §5D).
#
# Core naming convention: <name>_libretro.so + <name>_libretro.info
NONCOMMERCIAL_CORES="
  snes9x
  snes9x_next
  genesisplusgx
  genesisplusgx_expanded
  genesisplusgx-expanded
  genesisplusgx_wide
  genesisplusgx-wide
  fbneo
  fmsx
  mame078plus
  opera
  px68k
  quasi88
  picodrive
  hatarib
  zc210
"

# ROM files to exclude from a SHIP_SAFE distribution (trademark or redistribution
# right unconfirmed per docs/19 §7). The Doom shareware WAD, prboom.wad, Mr.Boom,
# pong1k2p.nes, and 2048.nes are retained (confirmed-OK redistribution terms).
TRADEMARK_ROMS="
  DonkeyKongClassic
  fix_it_felix
  Old-Towers
  SpaceTwins
  Reflectron
  Santatlantean
"

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

step "4/a Stamp version metadata into the image (SB-1.3)"
# Write the canonical GOSE_VERSION (from repo root VERSION file) into the image
# so the running OS can report it independently of gose_vm_server.py's VERSION
# constant (which the integrator must also update — see report note).
run "echo '$GOSE_VERSION' > '$WORK/mnt/system/gose/GOSE_VERSION'"
run "echo 'BATOCERA_VERSION=$BATOCERA_VERSION' >> '$WORK/mnt/system/gose/GOSE_VERSION'"

step "4/b Strip trademark/unconfirmed ROMs for SHIP_SAFE distribution (SB-1.4)"
# Remove ROM files whose redistribution right is unconfirmed or trademark-laden.
# Retained: doom1_shareware.wad (license file ships with it), prboom.wad (GPL),
# pong1k2p.nes + 2048.nes (homebrew, confirmed open-source), MrBoom.libretro (MIT).
# See docs/19-license-audit.md §7 for per-file rationale.
if [[ "$SHIP_SAFE" == "1" ]]; then
  for pattern in $TRADEMARK_ROMS; do
    # Use find to match any system subdirectory; the names are unique across systems
    if [[ $DRY_RUN -eq 1 ]]; then
      printf '  $ find %s/roms -iname "*%s*" -delete\n' "$WORK/mnt" "$pattern"
    else
      find "$WORK/mnt/roms" -iname "*${pattern}*" -delete 2>/dev/null || true
    fi
  done
  echo "  SHIP_SAFE ROM exclusion done (trademark/unconfirmed titles removed)"
else
  echo "  SHIP_SAFE=0 — skipping ROM exclusion (DEV_ONLY mode; do NOT distribute)"
fi

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

step "4c/6 Install the pre-ES shell autostart hook + strip non-commercial cores [BOOT partition]"
# How the shell AUTOSTARTS: Batocera launches the front-end via /usr/bin/emulationstation-
# standalone; GOSE swaps that script's ES launch line for `sh /userdata/gose-ui/gose-session.sh`.
# On the dev disk that edit lived in the Batocera overlay; a clean build has none, so we
# (re)apply it each boot from /boot/boot-custom.sh, which S00bootcustom runs BEFORE
# S31emulationstation. Idempotent + self-heals after an OS update. (boot = FAT partition p1.)
run "mount \${LOOP}p1 '$WORK/mnt'"
run "rsync -a '$LAYER/boot/boot-custom.sh' '$WORK/mnt/boot-custom.sh'"
run "sed -i 's/\\r\$//' '$WORK/mnt/boot-custom.sh'"

# SB-1.4: Strip non-commercial libretro cores from the Batocera squashfs.
# Batocera's root filesystem is a squashfs at /boot/boot/batocera (or
# /boot/boot/linux) on p1. The libretro cores live at /usr/lib/libretro/ inside
# it. We unpack the squashfs, remove the NONCOMMERCIAL_CORES, and repack.
#
# This is the build-time exclusion: the resulting image contains ZERO non-
# commercial cores. Users who want them for personal use can install them from
# libretro's own buildbot at runtime (they're not shipped in our paid depot).
# See docs/19-license-audit.md §5C for the "optional add-on" design.
#
# SHIP_SAFE=0 (dev-only): skips the strip; all stock Batocera cores present.
if [[ "$SHIP_SAFE" == "1" ]]; then
  # Locate the Batocera squashfs (name varies by release: batocera, linux, etc.)
  SQUASHFS=""
  for candidate in "$WORK/mnt/boot/batocera" "$WORK/mnt/boot/linux" "$WORK/mnt/batocera" "$WORK/mnt/linux"; do
    if [[ -f "$candidate" ]]; then SQUASHFS="$candidate"; break; fi
  done

  if [[ -z "$SQUASHFS" ]]; then
    echo "  WARNING: Batocera squashfs not found on p1 — cannot strip non-commercial cores."
    echo "           Check the boot partition layout for the pinned Batocera version."
    echo "           The image will contain all stock cores (including non-commercial ones)."
    echo "           Do NOT distribute this build commercially until the squashfs is stripped."
  elif [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] Would strip non-commercial cores from squashfs: $SQUASHFS"
    for core in $NONCOMMERCIAL_CORES; do
      printf '    rm -f squashfs-root/usr/lib/libretro/%s_libretro.so\n' "$core"
      printf '    rm -f squashfs-root/usr/share/libretro/info/%s_libretro.info\n' "$core"
    done
  else
    SQWORK="$WORK/squashfs-strip"
    run "rm -rf '$SQWORK'"
    # Unpack the squashfs (requires squashfs-tools: unsquashfs)
    run "unsquashfs -d '$SQWORK' '$SQUASHFS'"

    stripped=0
    for core in $NONCOMMERCIAL_CORES; do
      so="$SQWORK/usr/lib/libretro/${core}_libretro.so"
      info="$SQWORK/usr/share/libretro/info/${core}_libretro.info"
      if [[ -f "$so" ]]; then rm -f "$so"; stripped=$((stripped+1)); fi
      if [[ -f "$info" ]]; then rm -f "$info"; fi
    done
    echo "  stripped $stripped non-commercial core(s) from squashfs"

    # Repack (same compression as Batocera — gzip or lzo; mksquashfs defaults to gzip)
    SQUASHFS_NEW="${SQUASHFS}.new"
    run "mksquashfs '$SQWORK' '$SQUASHFS_NEW' -comp gzip -noappend -no-progress"
    run "mv -f '$SQUASHFS_NEW' '$SQUASHFS'"
    run "rm -rf '$SQWORK'"

    # Write a manifest of stripped cores to the userdata partition for auditability.
    # (p2 is already unmounted at this point — write to a temp file, copy when we
    #  can access userdata again. For now, log to build output only.)
    echo "  SHIP_SAFE core strip complete. Stripped cores (see docs/19 §5A):"
    for core in $NONCOMMERCIAL_CORES; do echo "    $core"; done
  fi
else
  echo "  SHIP_SAFE=0 — DEV_ONLY mode: non-commercial cores retained. Do NOT distribute."
fi

run "umount '$WORK/mnt'"
run "losetup -d \$LOOP"

step "5/6 Package an importable OVA [needs qemu-img]"
run "python3 '$HERE/make_ova.py' --image '$OUT_IMG' --name GOSE-PC --memory '$MEMORY_MB' --cpus '$CPUS' --out '$OUT_OVA'"

step "6/6 Done"
echo "    Run it:    python3 $REPO/scripts/gose_vm.py --image '$OUT_IMG' --share ~/roms"
echo "    Or import: $OUT_OVA  (VirtualBox/VMware: File > Import Appliance)"
