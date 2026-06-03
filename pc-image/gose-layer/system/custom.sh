#!/bin/bash
# GOSE layer autostart — Batocera runs /userdata/system/custom.sh with start/stop.
# The build (build-gose-pc.sh) copies this file + the repo's agent/ into the image.
GOSE=/userdata/system/gose
LOG=/userdata/system/logs/gose-agent.log

case "$1" in
  start)
    # Start the GOSE agent so Ava/Wren/Iris (and Claude) can drive the VM over TCP
    # 5555 — the QEMU launcher forwards that port (scripts/gose_vm.py).
    if [ -d "$GOSE/agent" ]; then
      ( cd "$GOSE/agent" && \
        GOSE_AGENT_TOKEN="${GOSE_AGENT_TOKEN:-}" \
        python3 -m gose_agent --host 0.0.0.0 --port 5555 >>"$LOG" 2>&1 & )
    fi
    ;;
  stop)
    pkill -f "gose_agent" 2>/dev/null || true
    ;;
esac
exit 0
