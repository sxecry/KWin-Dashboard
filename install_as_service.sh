#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_PATH="${SERVICE_DIR}/kwin-dashboard.service"
DEFAULT_HOST="0.0.0.0"
DEFAULT_PORT="8765"

read -rp "WebSocket host [${DEFAULT_HOST}]: " HOST
HOST="${HOST:-${DEFAULT_HOST}}"
read -rp "WebSocket port [${DEFAULT_PORT}]: " PORT
PORT="${PORT:-${DEFAULT_PORT}}"

mkdir -p "${SERVICE_DIR}"

cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=KWin Dashboard WebSocket service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/kwin_dashboard.py --ws --host ${HOST} --port ${PORT} --interval 1.0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now kwin-dashboard.service

echo "Installed and started kwin-dashboard.service"
echo "Disable it later with: systemctl --user disable --now kwin-dashboard.service"
