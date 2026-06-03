#!/usr/bin/env bash
# Install the GOSE Agent as a systemd service on the device. [needs hardware]
set -uo pipefail

GOSE_DIR="${GOSE_DIR:-/storage/gose}"
TOKEN_FILE="$GOSE_DIR/agent.token"

# Generate a token once and persist it (the AI host uses the same value).
if [ ! -f "$TOKEN_FILE" ]; then
  (command -v openssl >/dev/null && openssl rand -hex 16 || date +%s%N | sha256sum | cut -c1-32) \
    > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"

UNIT=/etc/systemd/system/gose-agent.service
cat > "$UNIT" <<EOF
[Unit]
Description=GOSE Agent (AI control daemon)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=GOSE_AGENT_HOST=0.0.0.0
Environment=GOSE_AGENT_PORT=8731
Environment=GOSE_AGENT_TOKEN=$TOKEN
Environment=GOSE_AGENT_ROMS_DIR=/storage/roms
WorkingDirectory=$GOSE_DIR/agent
ExecStart=/usr/bin/env python3 -m gose_agent
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now gose-agent.service
echo "[gose] agent service installed. Token stored at $TOKEN_FILE"
echo "[gose] connect from the AI host with GOSE_TOKEN=$TOKEN"
