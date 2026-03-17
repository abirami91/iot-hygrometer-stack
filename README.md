## 🚀 Quick Installation

Follow these commands on your Raspberry Pi.

```bash
git clone https://github.com/<your-username>/hygrometer-cloud.git
cd hygrometer-cloud
chmod +x startup.sh
./startup.sh
```
The script will automatically:

* check Docker installation

* enable Bluetooth

* create configuration files

* start all containers

* start the dashboard server

🌐 Open the Dashboard

After startup, open the dashboard in your browser:

```bash
http://<raspberry-pi-ip>:8081
```

Example:
```bash
http://192.168.1.44:8081
⚙️ Setup Page
```
## ⚙️ Setup Page

When opening the dashboard for the first time, a setup page appears automatically.

From this page you can:

1️⃣ Scan and select your hygrometer
2️⃣ Configure email reports
3️⃣ Send a test report

After setup is complete, the dashboard switches to normal monitoring mode.

## 📧 Email Configuration (Optional)

Email reports can be configured directly from the Setup page in the dashboard.

Enter your SMTP details:
```bash
Setting	Example
SMTP Host	smtp.gmail.com
SMTP Port	587
TLS	Enabled
Username	your@email.com

Password	App password
From Email	your@email.com

To Email	destination@email.com

Click Save Email Settings.
```
## 🧪 Send Test Report

After saving the email configuration, click Send Test Report to verify that email delivery works.

The system will:

* generate a sample report

* send it via SMTP

* confirm delivery in the dashboard

## Gmail Setup (Recommended)

If using Gmail:

* Enable 2-Factor Authentication

* Create an App Password

* Google Account → Security → App Passwords

* Use the generated password instead of your normal Google password.

## 🔄 Useful Commands

1. View running containers:
```bash
docker compose ps
⚙️ Setup Page
```
2. View logs:
```bash
docker compose logs -f
```
3. Restart system:
```bash
docker compose restart
```
4. Stop system:
```bash
docker compose down
```