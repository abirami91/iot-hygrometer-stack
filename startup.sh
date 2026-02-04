#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "========================================"
echo " Hygrometer Project — Startup"
echo "========================================"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "❌ Missing: $1"
    exit 1
  }
}

# 1) Prereqs
need_cmd docker
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker daemon not running."
  echo "Try: sudo systemctl start docker"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "❌ docker compose plugin not found."
  echo "Install: sudo apt-get update && sudo apt-get install -y docker-compose-plugin"
  exit 1
fi

need_cmd curl
need_cmd awk
need_cmd sed

# 2) Bluetooth service
echo "🔵 Ensuring Bluetooth is enabled..."
sudo systemctl enable bluetooth >/dev/null 2>&1 || true
sudo systemctl start bluetooth >/dev/null 2>&1 || true

# 3) Env file
if [[ ! -f ".env" ]]; then
  if [[ -f ".env.example" ]]; then
    echo "📄 Creating .env from .env.example"
    cp .env.example .env
  else
    echo "❌ .env.example not found. Please add it to the repo."
    exit 1
  fi
fi

# Helper: read var from .env
get_env() {
  local key="$1"
  awk -F= -v k="$key" '$1==k {print substr($0, index($0,$2))}' .env | tail -n 1
}

# Helper: set var in .env (create if missing)
set_env() {
  local key="$1"
  local val="$2"
  if grep -qE "^${key}=" .env; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

# 3.5) Ensure BIND_IP is set (Option A)
BIND_IP="$(get_env BIND_IP | tr -d '\r' || true)"
if [[ -z "${BIND_IP}" ]]; then
  BIND_IP="$(hostname -I | awk '{print $1}')"
  set_env BIND_IP "${BIND_IP}"
  echo "✅ Saved BIND_IP=${BIND_IP} to .env"
fi
BASE_URL="http://${BIND_IP}:8081"

# 3.6) Ensure DEVICE_MAC is set (still interactive for now)
DEVICE_MAC="$(get_env DEVICE_MAC | tr -d '\r' || true)"
if [[ -z "${DEVICE_MAC}" ]]; then
  echo ""
  echo "⚠️  DEVICE_MAC is not set in .env"
  echo "You can find it with:"
  echo "  bluetoothctl scan on"
  echo "  (look for LYWSD03MMC) then scan off"
  echo ""
  read -rp "Enter hygrometer MAC (example A4:C1:38:91:8A:0E): " DEVICE_MAC
  DEVICE_MAC="$(echo "$DEVICE_MAC" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"
  if [[ ! "$DEVICE_MAC" =~ ^([0-9A-F]{2}:){5}[0-9A-F]{2}$ ]]; then
    echo "❌ Invalid MAC format: $DEVICE_MAC"
    exit 1
  fi
  set_env DEVICE_MAC "$DEVICE_MAC"
  echo "✅ Saved DEVICE_MAC to .env"
fi

# 4) Data directory + permissions
echo "📂 Preparing data directory..."
mkdir -p data/archive data/insights data/reports
sudo chown -R "$USER":"$USER" data || true

# 4.5) Optional: archive snapshot (COPY, not MOVE)
# Use: STARTUP_ARCHIVE_SNAPSHOT=1 ./startup.sh
if [[ "${STARTUP_ARCHIVE_SNAPSHOT:-0}" == "1" ]]; then
  ts="$(date +%Y%m%d_%H%M%S)"
  if [[ -f "data/current.csv" ]]; then
    echo "📦 Snapshot current.csv -> data/archive/current_${ts}.csv"
    cp "data/current.csv" "data/archive/current_${ts}.csv" || true
  fi
  if [[ -f "data/hygro.db" ]]; then
    echo "📦 Snapshot hygro.db -> data/archive/hygro_${ts}.db"
    cp "data/hygro.db" "data/archive/hygro_${ts}.db" || true
  fi
fi

# IMPORTANT: ensure current.csv exists (so import endpoint always has a stable path)
# Collector will overwrite it when it runs.
if [[ ! -f "data/current.csv" ]]; then
  echo "📝 Creating placeholder data/current.csv (collector will update it)..."
  printf "timestamp,temperature_c,humidity_percent,battery_mv\n" > data/current.csv
fi

# 5) Start stack
echo "🐳 Starting containers..."
docker compose up -d --build

# 6) Wait for server
echo "⏳ Waiting for ${BASE_URL} ..."
for i in {1..30}; do
  if curl -fsS "${BASE_URL}/" >/dev/null 2>&1; then
    echo "✅ Server reachable."
    break
  fi
  echo "⏳ Not up yet ($i/30). Waiting 2s..."
  sleep 2
done

# 7) Import current.csv once now (best effort)
echo "📥 Importing current.csv into database..."
curl -s -X POST "${BASE_URL}/api/import-current" >/dev/null 2>&1 || true

# 8) Auto-import current.csv every 20 minutes (host cron)
# Default: install automatically (friend-proof)
# Opt-out: STARTUP_SKIP_CRON=1 ./startup.sh
CRON_LINE="*/20 * * * * curl -s -X POST ${BASE_URL}/api/import-current >/dev/null 2>&1"
CRON_TAG="# hygro-cloud auto-import"

if [[ "${STARTUP_SKIP_CRON:-0}" == "1" ]]; then
  echo "ℹ️  Skipping cron install (STARTUP_SKIP_CRON=1)."
else
  echo "🕒 Ensuring auto-import cron is installed (every 20 min)..."
  (
    crontab -l 2>/dev/null | grep -v "api/import-current" | grep -v "hygro-cloud auto-import" || true
    echo "$CRON_LINE $CRON_TAG"
  ) | crontab -
  echo "✅ Cron installed:"
  echo "   $CRON_LINE"
fi

# 9) Print URLs
echo ""
echo "========================================"
echo " 🎉 Hygrometer system is up!"
echo ""
echo " URL: ${BASE_URL}"
echo "========================================"
