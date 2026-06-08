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

exit 0
