# 🌡️ Hygrometer Cloud (Raspberry Pi)

A **self-hosted, privacy-first hygrometer monitoring system** built on Raspberry Pi.

It reads temperature, humidity, and battery data from a **Bluetooth (BLE) hygrometer**, stores the data locally, generates insights, and exposes a clean **web dashboard** accessible on your local network.

No cloud. No accounts. Your data stays on your Pi.

---

## ✨ Features

- 📡 **BLE Hygrometer support** (Xiaomi LYWSD03MMC / MiBeacon)
- 🐳 Fully **Dockerized** (collector, server, agent)
- 📊 Interactive **web dashboard** (Chart.js)
- 🧠 **Insights agent** (24h / 7d analysis, warnings & alerts)
- 🟡 Visual **status badge** (OK / WARN / ALERT)
- 🔒 **Private by default** (LAN-only access)
- 🧑‍🤝‍🧑 Friend-friendly one-command startup

---

## 🧱 Architecture (High level)

    BLE Hygrometer
    ↓
    Collector container (BLE → CSV)
    ↓
    SQLite database
    ↓
    Insights Agent (JSON summary)
    ↓
    FastAPI Server
    ↓
    Web Dashboard (Browser)


---

## 📂 Repository Structure
    ├── agent/
    │ ├── make_insights.py # Computes humidity / temperature insights
    │ └── Dockerfile
    │
    ├── collector/
    │ ├── gatt_collector.py # BLE data collection
    │ ├── adv_collector.py
    │ └── Dockerfile
    │
    ├── server/
    │ ├── app.py # FastAPI backend
    │ ├── static/
    │ │ └── app.js # Dashboard JS
    │ ├── templates/
    │ │ └── index.html # Dashboard UI
    │ └── Dockerfile
    │
    ├── data/
    │ ├── hygro.db # SQLite DB (auto-created)
    │ ├── current.csv # Latest sensor readings
    │ ├── insights/
    │ │ └── latest.json # Agent output
    │ └── archive/ # Auto-archived data
    │
    ├── docker-compose.yml
    ├── startup.sh # One-command startup
    ├── run.sh
    ├── .env.example
    └── README.md


---

## 🔧 Prerequisites

### Hardware
- Raspberry Pi (tested on Pi 4)
- BLE Hygrometer (e.g. Xiaomi LYWSD03MMC)

### Software
- Raspberry Pi OS / Debian-based Linux
- **Docker** (must be installed by the user)

---

## 🐳 Install Docker (Required)

If Docker is not installed, `startup.sh` will stop and tell you.

### Install Docker & Compose plugin:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

sudo apt-get update
sudo apt-get install -y docker-compose-plugin
```

Verify:

```bash
docker --version
docker compose version
```

🚀 Quick Start (Recommended)

Clone the repo and run 
```bash
chmod +x startup.sh
./startup.sh

```

What startup.sh does

✅ Checks Docker & Docker Compose

🔵 Enables Bluetooth service

📄 Creates .env from .env.example

📡 Prompts for hygrometer MAC address (once)

🧹 Archives old data (safe default)

🐳 Builds & starts all containers

🧠 Runs insights agent automatically

⏱ Installs cron job (auto-import every 20 min)

🌐 Prints dashboard URLs


## 🌐 Access the Dashboard

After startup:

* Local (Pi):
```bash
    http://localhost:8081
```
* From another device on same Wi-Fi:
```bash
    http://<pi-ip>:8081
```
The server is **LAN only**,  not exposed to the internet.


## 🧠 Insights Agent

The agent analyzes data every run and generates:

* 24h / 7d statistics

* Humidity thresholds

* Warning & alert status

Output file:
```bash
    data/insights/latest.json
```
    
Status logic:

🟢 ok → humidity safe

🟡 warn → >60% for extended time

🔴 alert → >65% for extended time

The dashboard badge updates automatically.

## ⚙️ Configuration

Edit .env if needed:
```bash
DEVICE_MAC=A4:C1:38:91:8A:0E
INTERVAL_SECONDS=1200
HUMIDITY_WARN=60
HUMIDITY_ALERT=65
```

## 🔄 Common Commands
```bash
docker compose ps
docker compose logs -f
docker compose down
docker compose up -d --build
```

## 🧩 Planned / Optional Extensions

💬 Chat-based agent inside dashboard

🍼 Baby room recommendations

🌬 Ventilation suggestions

📱 Mobile-friendly UI and expanding to multiple devices

🔔 Notifications (email / push)

🤖 Local LLM integration (optional)


## 🔐 Privacy & Security

No cloud services

No external APIs required

Data stored locally on Pi

LAN-only access

Fully inspectable source code


## 🧑‍💻 Author

Built with ❤️ as a personal IoT + learning project.

If you’re reading this as a friend:
plug in the Pi, run ./startup.sh, and you’re done 🙂

## 📜 License

MIT (or your preferred license)