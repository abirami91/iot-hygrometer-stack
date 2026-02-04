#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/iot-hygrometer-stack"
REPO_URL="https://github.com/abirami91/iot-hygrometer-stack.git"
APP_USER="${SUDO_USER:-pi}"

echo "========================================"
echo " Hygrometer Pi installer"
echo "========================================"

# 1) Base packages
sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  git \
  awk \
  sed \
  bluetooth \
  bluez

# 2) Install Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "🐳 Installing Docker..."
  curl -fsSL https://get.docker.com | sudo sh
fi

sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker "$APP_USER" || true

# 3) Enable Bluetooth
sudo systemctl enable bluetooth >/dev/null 2>&1 || true
sudo systemctl start bluetooth >/dev/null 2>&1 || true

# 4) Clone repo
if [[ ! -d "$APP_DIR/.git" ]]; then
  sudo mkdir -p "$APP_DIR"
  sudo chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
else
  sudo -u "$APP_USER" git -C "$APP_DIR" pull
fi

cd "$APP_DIR"

# 5) Ensure env exists (but DO NOT prompt here)
if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
fi

echo ""
echo "✅ Install complete."
echo "👉 Next:"
echo "   1) Re-login (or reboot) so docker group permissions apply"
echo "   2) Run: ./startup.sh"
