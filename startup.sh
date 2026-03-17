#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

echo "========================================"
echo " Hygrometer Project — Startup"
echo "========================================"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "❌ Missing command: $1"
    exit 1
  }
}

get_env() {
  local key="$1"
  [[ -f .env ]] || return 0
  awk -F= -v k="$key" '$1==k {print substr($0, index($0,$2))}' .env | tail -n 1
}

set_env() {
  local key="$1"
  local val="$2"
  if grep -qE "^${key}=" .env 2>/dev/null; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

wait_for_url() {
  local url="$1"
  local tries="${2:-30}"
  local delay="${3:-2}"

  echo "⏳ Waiting for ${url} ..."
  for ((i=1; i<=tries; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "✅ Server reachable."
      return 0
    fi
    echo "⏳ Not up yet (${i}/${tries}). Waiting ${delay}s..."
    sleep "$delay"
  done

  echo "⚠️ Server did not become reachable in time."
  return 1
}

ensure_cron_job() {
  local cron_line="$1"
  local cron_tag="$2"

  (
    crontab -l 2>/dev/null | grep -vF "$cron_tag" || true
    echo "${cron_line} ${cron_tag}"
  ) | crontab -

  echo "✅ Cron installed:"
  echo "   ${cron_line}"
}

# 1) Prerequisites
need_cmd docker
need_cmd curl
need_cmd awk
need_cmd sed

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

# 2) Bluetooth service
echo "🔵 Ensuring Bluetooth is enabled..."
sudo systemctl enable bluetooth >/dev/null 2>&1 || true
sudo systemctl start bluetooth >/dev/null 2>&1 || true

# 3) Ensure .env exists
if [[ ! -f ".env" ]]; then
  if [[ -f ".env.example" ]]; then
    echo "📄 Creating .env from .env.example"
    cp .env.example .env
  else
    echo "❌ .env.example not found."
    exit 1
  fi
fi

# 4) Ensure BIND_IP is set
BIND_IP="$(get_env BIND_IP | tr -d '\r' || true)"
if [[ -z "${BIND_IP}" ]]; then
  BIND_IP="$(hostname -I | awk '{print $1}')"
  if [[ -z "${BIND_IP}" ]]; then
    echo "❌ Could not determine BIND_IP automatically."
    exit 1
  fi
  set_env BIND_IP "${BIND_IP}"
  echo "✅ Saved BIND_IP=${BIND_IP} to .env"
fi

BASE_URL="http://${BIND_IP}:8081"

# 5) Data directories + permissions
echo "📂 Preparing data directory..."
mkdir -p data/archive data/insights data/reports
sudo chown -R "$USER":"$USER" data || true

# Optional snapshot of current data before startup
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

# Ensure stable placeholder file exists
if [[ ! -f "data/current.csv" ]]; then
  echo "📝 Creating placeholder data/current.csv ..."
  printf "timestamp,temperature_c,humidity_percent,battery_mv\n" > data/current.csv
fi

# 6) Start stack
echo "🐳 Starting containers..."
docker compose up -d --build

# 7) Wait for server
wait_for_url "${BASE_URL}/" 30 2 || true

# 8) Best-effort initial import
echo "📥 Importing current.csv into database..."
curl -s -X POST "${BASE_URL}/api/import-current" >/dev/null 2>&1 || true

# 9) Install cron for periodic import
CRON_LINE="*/20 * * * * curl -s -X POST ${BASE_URL}/api/import-current >/dev/null 2>&1"
CRON_TAG="# hygro-cloud auto-import"

if [[ "${STARTUP_SKIP_CRON:-0}" == "1" ]]; then
  echo "ℹ️ Skipping cron install (STARTUP_SKIP_CRON=1)."
else
  echo "🕒 Ensuring auto-import cron is installed..."
  ensure_cron_job "$CRON_LINE" "$CRON_TAG"
fi

# 10) Optional test email trigger
# Assumes email is already configured in the setup page
# Use: STARTUP_TEST_EMAIL=1 ./startup.sh
if [[ "${STARTUP_TEST_EMAIL:-0}" == "1" ]]; then
  echo ""
  echo "📧 Sending test report email..."
  TODAY="$(date +%Y-%m-%d)"

  docker exec -i hygro-reporter \
    sh -lc "SEND_EMAIL=1 REPORT_DATE=${TODAY} python /app/generate_and_send.py" \
    || echo "⚠️ Test email failed (see reporter logs)"
fi

# 11) Final output
echo ""
echo "========================================"
echo " 🎉 Hygrometer system is up!"
echo ""
echo " Complete setup here:"
echo " ${BASE_URL}/setup"
echo ""
echo " Dashboard:"
echo " ${BASE_URL}/"
echo "========================================"