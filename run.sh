#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

docker compose up -d --build

echo ""
echo "✅ Started."
echo "Dashboard: http://localhost:8081"
echo "From another device: http://<pi-ip>:8081"
