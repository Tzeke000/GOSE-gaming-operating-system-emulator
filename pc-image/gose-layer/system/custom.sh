#!/bin/bash
# GOSE layer autostart — Batocera runs /userdata/system/custom.sh with start/stop.
# The build (build-gose-pc.sh) copies this file + the repo's agent/ into the image.
GOSE=/userdata/system/gose
LOG=/userdata/system/logs/gose-agent.log

case "$1" in
  start)
    # Start the GOSE agent so Ava/Wren/Iris (and Claude) can drive the VM over TCP
    # 8731 — the agent's default port (gose_agent reads GOSE_AGENT_* env only; it
    # has no CLI flags), which scripts/gose_vm.py forwards to the host.
    if [ -d "$GOSE/agent" ]; then
      mkdir -p "$(dirname "$LOG")"
      # token for non-loopback (remote agent) clients; persisted out-of-repo on
      # /userdata so it survives reboots without committing a secret.
      [ -z "${GOSE_AGENT_TOKEN:-}" ] && [ -f "$GOSE/token" ] && GOSE_AGENT_TOKEN="$(cat "$GOSE/token")"
      # setsid + </dev/null so the agent survives the launching shell/SSH session.
      ( cd "$GOSE/agent" && \
        GOSE_AGENT_HOST=0.0.0.0 GOSE_AGENT_PORT=8731 \
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
    ;;
  stop)
    pkill -f "gose_agent" 2>/dev/null || true
    ;;
esac
exit 0
