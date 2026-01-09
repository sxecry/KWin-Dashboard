#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_PATH="${SERVICE_DIR}/kwin-dashboard.service"

mkdir -p "${SERVICE_DIR}"

cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=KWin Dashboard WebSocket service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/kwin_dashboard.py --ws --host 0.0.0.0 --port 8765 --interval 1.0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now kwin-dashboard.service

echo "Installed and started kwin-dashboard.service"
