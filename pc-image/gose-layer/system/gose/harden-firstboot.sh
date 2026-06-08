#!/bin/bash
# GOSE first-boot security hardener (Task #83, 2026-06-08).
#
# Run detached from custom.sh on every boot; idempotent (a normal boot after the
# first is effectively a no-op). It disables the LAN-exposed services that have NO
# single stable batocera.conf key across releases — the NFS server stack
# (nfsd / rpcbind / rpc.mountd / rpc.statd). SSH and Samba are handled in
# batocera.conf.gose (system.ssh.enabled=0 / system.samba.enabled=0); the random
# root password + iptables firewall come from system.security.enabled=1.
#
# Rationale: in the dev VM these listen on the guest's 0.0.0.0 but are unreachable
# (QEMU SLIRP forwards ONLY 8731+22, both loopback-bound on the host). On REAL
# hardware there is no such NAT/hostfwd, so every 0.0.0.0 listener is exposed to
# whatever Wi-Fi the device joins. rpcbind in particular is a classic remote-DoS /
# amplification surface and should not face a LAN by default.
#
# HONESTY/VERIFY: init-script names drift between Batocera releases. This stops the
# daemons by name (always effective for the running session) and best-effort
# persists by removing the exec bit on any matching /etc/init.d entry. Confirm the
# exact S-script names on the pinned Batocera version; add any that are missing.
set -u

log() { echo "[harden-firstboot] $*"; }

# 1) Stop the running NFS server stack (effective immediately, every boot).
for svc in nfsd rpc.mountd rpc.statd rpcbind rpc.idmapd; do
  pkill -x "$svc" 2>/dev/null || true
done
# Tear down kernel nfsd threads / exports if the control fs is mounted.
[ -d /proc/fs/nfsd ] && { exportfs -ua 2>/dev/null || true; }

# 2) Best-effort persist: disable the matching init scripts so they don't relaunch.
for s in /etc/init.d/*nfs* /etc/init.d/*rpcbind* /etc/init.d/*portmap*; do
  [ -f "$s" ] || continue
  "$s" stop 2>/dev/null || true
  chmod -x "$s" 2>/dev/null || true
  log "disabled init script: $s"
done

# 3) Sanity line for the log: what is still listening on a non-loopback iface.
if command -v ss >/dev/null 2>&1; then
  log "post-harden non-loopback TCP listeners:"
  ss -tlnH 2>/dev/null | awk '$4 !~ /127\.0\.0\.1|\[::1\]/ {print "  " $4 " " $6}'
fi

# 4) Controller enumeration log (pre-mortem #6, Task #94 — Xbox pad first-boot).
# Enumerate every /dev/input/event* that looks like a gamepad (has BTN_SOUTH or
# BTN_GAMEPAD) and log the name + driver so a downloader (or us) can diagnose
# "pad doesn't work on first boot" without plugging in a screen.
# This runs at S99 (custom.sh calls us detached), well after udev has settled.
log "--- controller enumeration ---"
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PYEOF'
import os, struct, fcntl, array

# ioctl constants for evdev
EVIOCGNAME = 0x81004506  # get name (256 bytes)
EVIOCGID   = 0x80084502  # get id: bus/vendor/product/version (4 x u16)
EVIOCGBIT_KEY = 0x81404501  # get EV_KEY bitfield (160 bytes for 0..1279)
BTN_SOUTH  = 0x130        # the canonical "A / Cross" button code
BTN_GAMEPAD = 0x130       # alias

def has_btn_south(fd):
    buf = array.array('B', [0] * 80)  # 80 bytes = 640 bits (covers code 0x1ff)
    try:
        fcntl.ioctl(fd, EVIOCGBIT_KEY, buf, True)
    except OSError:
        return False
    return bool(buf[BTN_SOUTH >> 3] & (1 << (BTN_SOUTH & 7)))

found = 0
for ev in sorted(os.listdir('/dev/input')):
    if not ev.startswith('event'):
        continue
    path = f'/dev/input/{ev}'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        continue
    try:
        # name
        nbuf = array.array('B', [0] * 256)
        try:
            fcntl.ioctl(fd, EVIOCGNAME, nbuf, True)
            name = nbuf.tobytes().rstrip(b'\x00').decode('utf-8', errors='replace')
        except OSError:
            name = '?'
        # id
        idbuf = array.array('H', [0] * 4)
        try:
            fcntl.ioctl(fd, EVIOCGID, idbuf, True)
            vid, pid = idbuf[1], idbuf[2]
        except OSError:
            vid, pid = 0, 0
        if has_btn_south(fd):
            print(f'[harden-firstboot] PAD {path}: "{name}" vid={vid:04x} pid={pid:04x}')
            # Note whether this is an Xbox pad (vid 0x045e = Microsoft)
            if vid == 0x045e:
                # Check if hid_xpadneo is loaded (BT) vs xpad (USB)
                driver = 'unknown'
                try:
                    import glob
                    bt_paths = glob.glob(f'/sys/class/input/{ev}/device/driver')
                    for p in bt_paths:
                        t = os.path.realpath(p)
                        driver = os.path.basename(t)
                except Exception:
                    pass
                print(f'[harden-firstboot]   ^ Microsoft Xbox pad (vid 045e) — driver: {driver}')
            found += 1
    finally:
        os.close(fd)

if found == 0:
    print('[harden-firstboot] no gamepads detected at boot (normal if no pad plugged in yet)')
print(f'[harden-firstboot] xpadneo module loaded: ' +
      ('yes' if os.path.exists('/sys/module/hid_xpadneo') else 'no (USB xpad covers wired; BT needs xpadneo)'))
PYEOF
else
  log "python3 not found — skipping controller enumeration"
fi

exit 0
