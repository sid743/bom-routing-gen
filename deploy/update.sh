#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the app.   bash deploy/update.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart bom-routing
sleep 2
systemctl is-active --quiet bom-routing && echo "Updated and running ($(git log -1 --format='%h %s'))." \
  || { echo "Service failed to start:"; sudo journalctl -u bom-routing -n 30 --no-pager; exit 1; }
