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

# 3.7) Optional SMTP setup
# Run interactive setup: STARTUP_CONFIGURE_SMTP=1 ./startup.sh
# Or non-interactive: set SMTP_* in .env manually.
if [[ "${STARTUP_CONFIGURE_SMTP:-0}" == "1" ]]; then
  echo ""
  echo "========================================"
  echo " 📧 SMTP Setup (optional)"
  echo "========================================"
  echo "Tip (Gmail): use an App Password, not your normal password."
  echo "Leave SMTP_HOST empty to skip email setup."
  echo ""

  # Always read current values (so we don't overwrite)
  SMTP_HOST="$(get_env SMTP_HOST | tr -d '\r' || true)"
  SMTP_PORT="$(get_env SMTP_PORT | tr -d '\r' || true)"
  SMTP_USER="$(get_env SMTP_USER | tr -d '\r' || true)"
  SMTP_PASS="$(get_env SMTP_PASS | tr -d '\r' || true)"
  SMTP_FROM="$(get_env SMTP_FROM | tr -d '\r' || true)"
  SMTP_TO="$(get_env SMTP_TO | tr -d '\r' || true)"
  SMTP_TLS="$(get_env SMTP_TLS | tr -d '\r' || true)"

  # Prompt host first; if empty -> skip whole SMTP setup
  if [[ -z "${SMTP_HOST}" ]]; then
    read -rp "SMTP_HOST (e.g., smtp.gmail.com) [empty = skip]: " SMTP_HOST
    SMTP_HOST="$(echo "${SMTP_HOST}" | tr -d ' ')"
    [[ -n "${SMTP_HOST}" ]] && set_env SMTP_HOST "${SMTP_HOST}"
  fi

  if [[ -z "${SMTP_HOST}" ]]; then
    echo "ℹ️  SMTP setup skipped."
  else
    # Defaults (only if missing)
    [[ -z "${SMTP_PORT}" ]] && SMTP_PORT="587"
    [[ -z "${SMTP_TLS}"  ]] && SMTP_TLS="1"

    read -rp "SMTP_PORT [${SMTP_PORT}]: " _in
    [[ -n "${_in}" ]] && SMTP_PORT="${_in}"
    set_env SMTP_PORT "${SMTP_PORT}"

    read -rp "SMTP_TLS (1=starttls, 0=none) [${SMTP_TLS}]: " _in
    [[ -n "${_in}" ]] && SMTP_TLS="${_in}"
    set_env SMTP_TLS "${SMTP_TLS}"

    if [[ -z "${SMTP_USER}" ]]; then
      read -rp "SMTP_USER (email address): " SMTP_USER
      [[ -n "${SMTP_USER}" ]] && set_env SMTP_USER "${SMTP_USER}"
    fi

    if [[ -z "${SMTP_PASS}" ]]; then
      read -rsp "SMTP_PASS (app password recommended): " SMTP_PASS
      echo ""
      [[ -n "${SMTP_PASS}" ]] && set_env SMTP_PASS "${SMTP_PASS}"
    fi

    if [[ -z "${SMTP_TO}" ]]; then
      read -rp "SMTP_TO (recipient email, comma-separated ok): " SMTP_TO
      [[ -n "${SMTP_TO}" ]] && set_env SMTP_TO "${SMTP_TO}"
    fi

    # Default FROM to USER if missing
    SMTP_FROM="$(get_env SMTP_FROM | tr -d '\r' || true)"
    SMTP_USER="$(get_env SMTP_USER | tr -d '\r' || true)"
    if [[ -z "${SMTP_FROM}" && -n "${SMTP_USER}" ]]; then
      set_env SMTP_FROM "${SMTP_USER}"
    fi

    echo "✅ SMTP values saved in .env"
  fi
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

# 7.5) Optional: send test report email immediately
# Use: STARTUP_TEST_EMAIL=1 ./startup.sh
if [[ "${STARTUP_TEST_EMAIL:-0}" == "1" ]]; then
  echo ""
  echo "📧 Sending test report email..."

  TODAY="$(date +%Y-%m-%d)"

  docker exec -i hygro-reporter \
    sh -lc "SEND_EMAIL=1 REPORT_DATE=${TODAY} python /app/generate_and_send.py" \
    || echo "⚠️  Test email failed (see reporter logs)"

  echo "✅ Test email triggered."
fi


# 9) Print URLs
echo ""
echo "========================================"
echo " 🎉 Hygrometer system is up!"
echo ""
echo " URL: ${BASE_URL}"
echo "========================================"
