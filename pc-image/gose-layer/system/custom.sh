#!/bin/bash
# GOSE layer autostart — Batocera runs /userdata/system/custom.sh with start/stop.
# The build (build-gose-pc.sh) copies this file + the repo's agent/ into the image.
GOSE=/userdata/system/gose
LOG=/userdata/system/logs/gose-agent.log

case "$1" in
  start)
    # Start the GOSE agent so AI agents (and Claude) can drive the VM over TCP
    # 8731 — the agent's default port (gose_agent reads GOSE_AGENT_* env only; it
    # has no CLI flags), which scripts/gose_vm.py forwards to the host.
    if [ -d "$GOSE/agent" ]; then
      mkdir -p "$(dirname "$LOG")"
      # token for non-loopback (remote agent) clients; persisted out-of-repo on
      # /userdata so it survives reboots without committing a secret.
      # FIRST BOOT: if no token exists yet, GENERATE a unique per-install one so every
      # downloaded device gets its OWN secret (never a repo/baked literal). Written
      # mode-600 BEFORE the agent starts; the UI server reads the SAME file, so the
      # agent + UI converge on one token. (-s = exists and non-empty.)
      if [ ! -s "$GOSE/token" ]; then
        mkdir -p "$GOSE"
        ( umask 077; python3 -c 'import secrets; print(secrets.token_hex(16))' > "$GOSE/token" )
        chmod 600 "$GOSE/token" 2>/dev/null || true
      fi
      [ -z "${GOSE_AGENT_TOKEN:-}" ] && [ -f "$GOSE/token" ] && GOSE_AGENT_TOKEN="$(cat "$GOSE/token")"
      # Bind address (security, Task #83): LOOPBACK by default so on real hardware
      # the agent is NOT LAN-exposed (remote access is via Tailscale, tailnet-only).
      # The dev VM uses QEMU SLIRP user-net + a host hostfwd, where the forwarded
      # connection arrives on eth0 (10.0.2.15) from the SLIRP gateway 10.0.2.2 — so
      # the agent MUST bind 0.0.0.0 there or the host can't reach it. Auto-detect
      # that case (default route via 10.0.2.2) so the dev workflow survives a rebuild;
      # a real-hardware user who genuinely wants LAN exposure sets the opt-in flag.
      AGENT_HOST=127.0.0.1
      if ip route 2>/dev/null | grep -q 'default via 10\.0\.2\.2' \
         || [ -f "$GOSE/.agent-lan" ]; then
        AGENT_HOST=0.0.0.0
      fi
      # setsid + </dev/null so the agent survives the launching shell/SSH session.
      ( cd "$GOSE/agent" && \
        GOSE_AGENT_HOST="$AGENT_HOST" GOSE_AGENT_PORT=8731 \
        GOSE_AGENT_TOKEN="${GOSE_AGENT_TOKEN:-}" \
        setsid python3 -m gose_agent >>"$LOG" 2>&1 </dev/null & )
    fi
    # Provision the docs/25 §4 baked default app set (Steam/Firefox/Chromium/VLC/
    # Obsidian) + Firefox-as-default-browser. Idempotent: a normal boot is a no-op;
    # a fresh image / factory reset re-installs whatever is missing. Detached so it
    # never blocks the shell coming up.
    if [ -x "$GOSE/provision-baked-apps.sh" ]; then
      setsid "$GOSE/provision-baked-apps.sh" </dev/null >/dev/null 2>&1 &
    fi
    # Security hardener (Task #83): disable the LAN-exposed NFS server stack that
    # has no stable batocera.conf key. Idempotent, detached, best-effort.
    if [ -x "$GOSE/harden-firstboot.sh" ]; then
      setsid "$GOSE/harden-firstboot.sh" </dev/null >>"$LOG" 2>&1 &
    fi
    ;;
  stop)
    pkill -f "gose_agent" 2>/dev/null || true
    ;;
esac
exit 0
