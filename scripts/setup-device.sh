#!/usr/bin/env bash
# GOSE device setup — idempotent. Run on the Odin 2 (over SSH) after the base
# distro (ROCKNIX/Batocera) boots. Re-runnable: re-flash -> run this -> restored.
#
#   ssh root@<odin-ip> 'bash -s' < scripts/setup-device.sh
#
# Everything here is [needs hardware]; it's a no-op-safe scaffold that we flesh
# out as real customizations are added. Each step is guarded so partial runs and
# missing tools don't break the whole script.
set -uo pipefail

log() { printf '\033[1;36m[gose]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[gose:warn]\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

GOSE_DIR="${GOSE_DIR:-/storage/gose}"

log "GOSE device setup starting (target: $GOSE_DIR)"

# 1) Identify the OS so later steps can branch (ROCKNIX vs Batocera).
if [ -f /etc/os-release ]; then . /etc/os-release; log "OS: ${PRETTY_NAME:-unknown}"; fi

# 2) Enable SSH (hacker layer). Most distros expose this via their settings; this
#    is a placeholder for distro-specific enablement.
log "SSH: ensure enabled via distro settings (placeholder)."

# 3) Install the GOSE Agent.
mkdir -p "$GOSE_DIR"
if [ -d "$(dirname "$0")/../agent" ]; then
  log "Copying agent -> $GOSE_DIR/agent"
  cp -r "$(dirname "$0")/../agent" "$GOSE_DIR/" 2>/dev/null || warn "copy skipped (run from repo)"
fi
if have pip3; then
  log "Installing evdev (real input injection)"
  pip3 install --user evdev >/dev/null 2>&1 || warn "evdev install failed (mock backend will be used)"
else
  warn "pip3 not found; agent will run with mock input backend"
fi

# 4) Install the agent service (systemd if present).
if have systemctl && [ -f "$(dirname "$0")/install-agent.sh" ]; then
  bash "$(dirname "$0")/install-agent.sh" || warn "service install skipped"
fi

# 5) Emulator default configs  [TODO: drop per-system sane defaults here]
log "Emulator defaults: TODO (PSP first — see ROADMAP)."

# 6) Windows-like front-end theme  [TODO: install gui/theme-windows]
log "Front-end theme: TODO (see gui/ and docs/06-gui-plan.md)."

log "Done. Verify with:  python3 $GOSE_DIR/agent/client/cli.py ping"
