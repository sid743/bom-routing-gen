#!/usr/bin/env bash
# One-time (re-runnable) setup of the BOM & Routing Generator on Ubuntu 24.04.
#
#   sudo bash deploy/setup.sh <domain>
#   e.g. sudo bash deploy/setup.sh bomrouting-sid.centralindia.cloudapp.azure.com
#
# Installs Python deps in a venv, runs the app with gunicorn as a systemd service
# (auto-restarts, starts on boot), and puts Caddy in front for automatic HTTPS and
# a username/password login. Data/history is kept in /var/lib/bom-routing.
set -euo pipefail

DOMAIN="${1:-}"
if [[ $EUID -ne 0 ]]; then echo "Run with sudo:  sudo bash deploy/setup.sh <domain>"; exit 1; fi
if [[ -z "$DOMAIN" ]]; then echo "Usage: sudo bash deploy/setup.sh <domain>"; exit 1; fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$APP_DIR")}"
DATA_DIR=/var/lib/bom-routing
PORT=5003

echo "==> App folder: $APP_DIR   (service user: $RUN_USER, data: $DATA_DIR)"

echo "==> Site login (you'll type this in the browser to open the app)"
read -rp "    Username: " SITE_USER
while true; do
  read -rsp "    Password: " SITE_PW; echo
  read -rsp "    Repeat password: " SITE_PW2; echo
  [[ -n "$SITE_PW" && "$SITE_PW" == "$SITE_PW2" ]] && break
  echo "    Passwords empty or don't match - try again."
done

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv git curl debian-keyring debian-archive-keyring apt-transport-https

if ! swapon --show | grep -q .; then
  echo "==> Adding 1 GB swap (VM has only 1 GB RAM)"
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Python environment"
sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

mkdir -p "$DATA_DIR"
chown "$RUN_USER:$RUN_USER" "$DATA_DIR"

echo "==> App service (bom-routing)"
cat > /etc/systemd/system/bom-routing.service <<UNIT
[Unit]
Description=BOM & Routing Generator
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
Environment=BRG_DATA=$DATA_DIR
ExecStart=$APP_DIR/.venv/bin/gunicorn --chdir $APP_DIR --workers 1 --threads 4 --timeout 120 --bind 127.0.0.1:$PORT bom_routing_app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now bom-routing
systemctl restart bom-routing

echo "==> Caddy (HTTPS + login)"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi
HASH="$(caddy hash-password --plaintext "$SITE_PW")"
unset SITE_PW SITE_PW2
{
  printf '%s {\n' "$DOMAIN"
  printf '\tbasic_auth {\n\t\t%s %s\n\t}\n' "$SITE_USER" "$HASH"
  printf '\trequest_body {\n\t\tmax_size 25MB\n\t}\n'
  printf '\tencode gzip\n'
  printf '\treverse_proxy 127.0.0.1:%s\n' "$PORT"
  printf '}\n'
} > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy

echo "==> Checking"
for _ in $(seq 1 15); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)
  [[ "$code" == "200" ]] && break; sleep 1
done
if [[ "$code" == "200" ]]; then echo "    app service: running"; else echo "    app service NOT responding - see: sudo journalctl -u bom-routing -n 50"; fi

echo
echo "Done. Open:  https://$DOMAIN   (login: $SITE_USER)"
echo "First visit can take ~30 s while Caddy gets the HTTPS certificate."
echo "Update later with:  bash deploy/update.sh"
