import csv
import io
import asyncio
import os
import sqlite3
from datetime import datetime, date
from typing import List, Optional
import subprocess
import re
from fastapi import HTTPException
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pathlib import Path
import json

import json
from fastapi.responses import FileResponse
from fastapi import Query
import smtplib
import ssl
from email.message import EmailMessage

DATA_DIR = os.getenv("DATA_DIR", "/data")

DB_PATH = os.path.join(DATA_DIR, "hygro.db")
CSV_CURRENT = os.path.join(DATA_DIR, "current.csv")
SETUP_CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
INSIGHTS_PATH = os.path.join(DATA_DIR, "insights", "latest.json")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "insights"), exist_ok=True)
os.makedirs("static/uploads", exist_ok=True)


class EmailConfigReq(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls: bool = True
    smtp_user: str = ""
    smtp_pass: str = ""
    mail_from: str = ""
    mail_to: str = ""

class IngestReadingReq(BaseModel):
    mac: str
    ts_utc: str
    epoch: int
    temp_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    battery_mv: Optional[int] = None

class SelectDeviceReq(BaseModel):
    mac: str
    name: Optional[str] = None
    
def _default_v2_config() -> dict:
    return {
        "schema_version": 2,
        "rooms": [
            # start with an empty default room for new users
            # existing users will get migrated to this list
            {"id": "default", "label": "Default", "mac": "", "name": None, "enabled": True}
        ],
        "email": {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_tls": True,
            "smtp_user": "",
            "smtp_pass": "",
            "mail_from": "",
            "mail_to": ""
        }
    }
    


def load_config_v2() -> dict:
    """
    Always returns schema_version=2 config.
    Migrates old config.json formats automatically.
    """
    raw = _load_setup_cfg()
    if not raw:
        # new user
        cfg = _default_v2_config()
        _save_setup_cfg(cfg)
        return cfg

    # Already v2
    if raw.get("schema_version") == 2 and isinstance(raw.get("rooms"), list):
        # Ensure required keys exist (forward compatible)
        if "email" not in raw:
            raw["email"] = _default_v2_config()["email"]
            _save_setup_cfg(raw)
        return raw

    # ---- Migrate v1 -> v2 ----
    # v1 shape: {"device_mac": "...", "device_name": "..."}
    mac = (raw.get("device_mac") or raw.get("DEVICE_MAC") or "").strip().upper()
    name = (raw.get("device_name") or "").strip() or None

    cfg = _default_v2_config()
    if mac:
        cfg["rooms"] = [
            {"id": "default", "label": "Default", "mac": mac, "name": name, "enabled": True}
        ]

    _save_setup_cfg(cfg)
    return cfg

def room_id_for_mac(cfg: dict, mac: str) -> Optional[str]:
    m = (mac or "").strip().upper()
    if not m:
        return None
    for r in (cfg.get("rooms") or []):
        if not r.get("enabled", True):
            continue
        if (r.get("mac") or "").strip().upper() == m:
            return (r.get("id") or "").strip() or None
    return None

def get_primary_room(cfg: dict) -> dict:
    """
    Primary room for backward compatibility:
    - first enabled room with a mac
    - else 'default' room if present
    - else first room
    - else synthetic default
    """
    rooms = cfg.get("rooms") or []

    # first enabled room with configured mac
    for r in rooms:
        if r.get("enabled", True) and (r.get("mac") or "").strip():
            return r

    # fallback to default id
    for r in rooms:
        if (r.get("id") or "").strip() == "default":
            return r

    if rooms:
        return rooms[0]

    return {"id": "default", "label": "Default", "mac": "", "name": None, "enabled": True}

def get_room_or_404(cfg: dict, room_id: str) -> dict:
    rooms = cfg.get("rooms") or []
    for r in rooms:
        if (r.get("id") or "").strip() == room_id:
            return r
    raise HTTPException(status_code=404, detail=f"Unknown room_id: {room_id}")

def get_email_config() -> dict:
    cfg = load_config_v2()
    email = cfg.get("email") or {}
    default_email = _default_v2_config()["email"]
    return {
        "enabled": bool(email.get("enabled", default_email["enabled"])),
        "smtp_host": str(email.get("smtp_host", default_email["smtp_host"])),
        "smtp_port": int(email.get("smtp_port", default_email["smtp_port"])),
        "smtp_tls": bool(email.get("smtp_tls", default_email["smtp_tls"])),
        "smtp_user": str(email.get("smtp_user", default_email["smtp_user"])),
        "smtp_pass": str(email.get("smtp_pass", default_email["smtp_pass"])),
        "mail_from": str(email.get("mail_from", default_email["mail_from"])),
        "mail_to": str(email.get("mail_to", default_email["mail_to"])),
    }

def save_email_config(email_cfg: dict) -> dict:
    cfg = load_config_v2()
    cfg["email"] = {
        "enabled": bool(email_cfg.get("enabled", False)),
        "smtp_host": str(email_cfg.get("smtp_host", "")).strip(),
        "smtp_port": int(email_cfg.get("smtp_port", 587)),
        "smtp_tls": bool(email_cfg.get("smtp_tls", True)),
        "smtp_user": str(email_cfg.get("smtp_user", "")).strip(),
        "smtp_pass": str(email_cfg.get("smtp_pass", "")).strip(),
        "mail_from": str(email_cfg.get("mail_from", "")).strip(),
        "mail_to": str(email_cfg.get("mail_to", "")).strip(),
    }
    _save_setup_cfg(cfg)
    return cfg["email"]

def send_email_with_attachment_from_config(file_path: str):
    email = get_email_config()

    if not email.get("enabled"):
        raise RuntimeError("Email not enabled")

    smtp_host = email.get("smtp_host", "").strip()
    smtp_port = int(email.get("smtp_port", 587))
    smtp_tls = bool(email.get("smtp_tls", True))
    smtp_user = email.get("smtp_user", "").strip()
    smtp_pass = email.get("smtp_pass", "").strip()
    mail_from = email.get("mail_from", "").strip()
    mail_to = email.get("mail_to", "").strip()

    if not smtp_host:
        raise RuntimeError("SMTP host missing")
    if not mail_from:
        raise RuntimeError("From email missing")
    if not mail_to:
        raise RuntimeError("To email missing")

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]
    if not recipients:
        raise RuntimeError("No valid recipients configured")

    fp = Path(file_path)
    if not fp.exists():
        raise RuntimeError("Attachment file not found")

    msg = EmailMessage()
    msg["Subject"] = f"Hygrometer Report - {fp.name}"
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(f"Attached is the latest hygrometer report: {fp.name}")

    data = fp.read_bytes()
    if fp.suffix.lower() == ".pdf":
        maintype, subtype = "application", "pdf"
    elif fp.suffix.lower() == ".zip":
        maintype, subtype = "application", "zip"
    else:
        maintype, subtype = "application", "octet-stream"

    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fp.name)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
        if smtp_tls:
            s.starttls(context=ssl.create_default_context())
        if smtp_user:
            s.login(smtp_user, smtp_pass)
        s.send_message(msg)
        

def build_room_status(cfg: dict, room: dict, stale_seconds: int) -> dict:
    room_id = (room.get("id") or "").strip()
    label = (room.get("label") or room_id or "Unknown").strip()
    mac = (room.get("mac") or "").strip()
    enabled = bool(room.get("enabled", True))

    base = {
        "room_id": room_id,
        "label": label,
        "mac": mac,
        "name": room.get("name"),
        "enabled": enabled,
        "configured": bool(mac),
        "status": "not_configured",
        "message": None,
        "age_seconds": None,
        "reading": None,
    }

    if not mac:
        base["message"] = f"Room '{room_id}' has no hygrometer configured yet."
        return base

    conn = get_db()
    try:
        row = fetch_latest_row(conn, room_id)
    finally:
        conn.close()

    if not row:
        base["status"] = "no_data"
        base["message"] = f"No readings found yet for room '{room_id}'."
        return base

    keys = ["ts_utc", "epoch", "temp_c", "humidity_pct", "battery_mv"]
    reading = dict(zip(keys, row))
    age_seconds = calc_age_seconds(reading.get("epoch"))

    base["reading"] = reading
    base["age_seconds"] = age_seconds

    if age_seconds is None:
        base["status"] = "stale"
        base["message"] = "Cannot determine data age."
        return base

    if age_seconds > stale_seconds:
        base["status"] = "stale"
        base["message"] = f"Last update was {age_seconds} seconds ago."
        return base

    base["status"] = "ok"
    return base

def fetch_latest_row(conn: sqlite3.Connection, room_id: str):
    cur = conn.execute(
        "SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv FROM readings WHERE room_id=? ORDER BY epoch DESC LIMIT 1",
        (room_id,)
    )
    return cur.fetchone()

def calc_age_seconds(epoch_val) -> Optional[int]:
    now_epoch = int(datetime.utcnow().timestamp())
    try:
        return now_epoch - int(epoch_val or now_epoch)
    except Exception:
        return None

def save_rooms_v2(rooms: list[dict]) -> dict:
    cfg = load_config_v2()

    # Minimal validation
    cleaned = []
    seen_ids = set()
    for r in rooms:
        rid = (r.get("id") or "").strip()
        label = (r.get("label") or "").strip()
        mac = (r.get("mac") or "").strip().upper()
        name = (r.get("name") or None)
        enabled = bool(r.get("enabled", True))

        if not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)
        if not label:
            label = rid

        cleaned.append({"id": rid, "label": label, "mac": mac, "name": name, "enabled": enabled})

    cfg["rooms"] = cleaned
    _save_setup_cfg(cfg)
    return cfg

def _load_setup_cfg() -> dict:
    if not os.path.exists(SETUP_CONFIG_PATH):
        return {}
    try:
        with open(SETUP_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _save_setup_cfg(cfg: dict) -> None:
    os.makedirs(os.path.dirname(SETUP_CONFIG_PATH), exist_ok=True)
    with open(SETUP_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

app = FastAPI(title="Hygrometer Cloud") 

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
async def _auto_import_current_csv():
    async def loop():
        while True:
            try:
                import_current_csv()  # your existing endpoint function
            except Exception as e:
                print("[WARN] auto-import failed:", e, flush=True)
            await asyncio.sleep(60)

    asyncio.create_task(loop())

@app.get("/api/setup/status")
def setup_status():
    cfg = load_config_v2()
    rooms = cfg.get("rooms") or []
    any_configured = any((r.get("mac") or "").strip() for r in rooms)

    # Backwards compatible response for your current UI
    # "configured" means at least one room has a MAC.
    primary = next((r for r in rooms if (r.get("mac") or "").strip()), None)

    return {
        "configured": bool(any_configured),
        "device": {
            "mac": (primary.get("mac") if primary else None),
            "name": (primary.get("name") if primary else None)
        } if primary else None,
        # New data for future UI:
        "rooms": rooms
    }

@app.get("/api/setup/devices")
def scan_ble_devices():
    env = {**os.environ, "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/run/dbus/system_bus_socket"}

    # 1) Trigger scan for 12s (fills bluetoothctl cache)
    subprocess.run(
        ["bluetoothctl", "--timeout", "12", "scan", "on"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    # 2) List known devices from cache
    proc = subprocess.run(
        ["bluetoothctl", "devices"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    # lines: "Device AA:BB:CC:DD:EE:FF Name"
    dev_line = re.compile(r"^Device\s+([0-9A-F:]{17})\s+(.+)$", re.IGNORECASE)

    devices = []
    for line in out.splitlines():
        m = dev_line.match(line.strip())
        if not m:
            continue
        mac = m.group(1).upper()
        name = m.group(2).strip()
        if name == mac or name.replace(":", "-").upper() == mac.replace(":", "-"):
            name = None

        # 3) Try get RSSI from bluetoothctl info
        rssi = None
        info = subprocess.run(
            ["bluetoothctl", "info", mac],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        ).stdout or ""
        m_rssi = re.search(r"RSSI:\s*(-?\d+)", info)
        if m_rssi:
            rssi = int(m_rssi.group(1))

        devices.append({"mac": mac, "name": name, "rssi": rssi})

    # Strongest RSSI first; named devices first
    devices.sort(key=lambda d: (d["rssi"] is None, -(d["rssi"] or -999), d["name"] is None))

    return {"devices": devices}

@app.get("/api/overview")
def api_overview():
    cfg = load_config_v2()
    rooms = cfg.get("rooms") or []
    stale_seconds = int(os.getenv("STALE_SECONDS", "600"))

    room_items = [build_room_status(cfg, room, stale_seconds) for room in rooms]

    summary = {
        "configured_rooms": sum(1 for r in room_items if r["configured"]),
        "enabled_rooms": sum(1 for r in room_items if r["enabled"]),
        "ok_rooms": sum(1 for r in room_items if r["status"] == "ok"),
        "stale_rooms": sum(1 for r in room_items if r["status"] == "stale"),
        "no_data_rooms": sum(1 for r in room_items if r["status"] == "no_data"),
        "not_configured_rooms": sum(1 for r in room_items if r["status"] == "not_configured"),
    }

    return {
        "status": "ok",
        "summary": summary,
        "rooms": room_items,
    }

class RoomsSaveReq(BaseModel):
    rooms: list[dict]

@app.get("/api/setup/config")
def setup_config():
    return load_config_v2()

@app.post("/api/setup/rooms")
def setup_rooms(req: RoomsSaveReq):
    cfg = save_rooms_v2(req.rooms)
    return {"ok": True, "config": cfg}

@app.get("/api/rooms")
def api_rooms():
    cfg = load_config_v2()
    rooms = cfg.get("rooms") or []
    primary = get_primary_room(cfg)
    return {
        "status": "ok",
        "message": None,
        "primary_room_id": (primary.get("id") or "default"),
        "rooms": rooms
    }
    
@app.get("/api/rooms/{room_id}/latest")
def api_room_latest(room_id: str):
    cfg = load_config_v2()
    room = get_room_or_404(cfg, room_id)

    configured_mac = (room.get("mac") or "").strip()
    if not configured_mac:
        return {
            "status": "not_configured",
            "message": f"Room '{room_id}' has no hygrometer configured yet.",
            "age_seconds": None,
            "reading": None,
        }

    conn = get_db()
    try:
        row = fetch_latest_row(conn, room_id)
    finally:
        conn.close()

    if not row:
        return {
            "status": "no_data",
            "message": f"No readings found yet for room '{room_id}'. Make sure the collector is running.",
            "age_seconds": None,
            "reading": None,
        }

    keys = ["ts_utc", "epoch", "temp_c", "humidity_pct", "battery_mv"]
    reading = dict(zip(keys, row))

    age_seconds = calc_age_seconds(reading.get("epoch"))
    stale_seconds = int(os.getenv("STALE_SECONDS", "600"))

    if age_seconds is None:
        return {
            "status": "stale",
            "message": "Cannot determine data age. Collector may not be updating.",
            "age_seconds": None,
            "reading": reading,
        }

    if age_seconds > stale_seconds:
        return {
            "status": "stale",
            "message": f"Last update was {age_seconds} seconds ago. Collector may not be receiving the device {configured_mac}.",
            "age_seconds": age_seconds,
            "reading": reading,
        }

    return {
        "status": "ok",
        "message": None,
        "age_seconds": age_seconds,
        "reading": reading,
    }
    

@app.post("/api/reports/send-latest")
def send_latest_report():
    base = Path(REPORTS_DIR)
    base.mkdir(parents=True, exist_ok=True)

    files = sorted(
        list(base.glob("**/*.pdf")) + list(base.glob("**/*.zip")),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    if not files:
        return {"ok": False, "error": "No reports found"}

    latest = files[0]

    try:
        send_email_with_attachment_from_config(str(latest))
        return {"ok": True, "filename": latest.name}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    
@app.get("/api/setup/email")
def setup_email_get():
    return {"ok": True, "email": get_email_config()}

@app.post("/api/setup/email")
def setup_email_save(req: EmailConfigReq):
    saved = save_email_config(req.dict())
    return {"ok": True, "email": saved}

@app.post("/api/setup/test-email")
async def test_email():
    email = get_email_config()

    if not email.get("enabled"):
        return {"ok": False, "error": "Email not enabled"}

    smtp_host = email.get("smtp_host", "").strip()
    smtp_port = int(email.get("smtp_port", 587))
    smtp_tls = bool(email.get("smtp_tls", True))
    smtp_user = email.get("smtp_user", "").strip()
    smtp_pass = email.get("smtp_pass", "").strip()
    mail_from = email.get("mail_from", "").strip()
    mail_to = email.get("mail_to", "").strip()

    if not smtp_host:
        return {"ok": False, "error": "SMTP host missing"}
    if not mail_from:
        return {"ok": False, "error": "From email missing"}
    if not mail_to:
        return {"ok": False, "error": "To email missing"}

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]
    if not recipients:
        return {"ok": False, "error": "No valid recipients configured"}

    try:
        msg = EmailMessage()
        msg["Subject"] = "Hygrometer Test Email"
        msg["From"] = mail_from
        msg["To"] = ", ".join(recipients)
        msg.set_content("This is a test email from your Raspberry Pi hygrometer system.")

        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
            if smtp_tls:
                s.starttls(context=ssl.create_default_context())
            if smtp_user:
                s.login(smtp_user, smtp_pass)
            s.send_message(msg)

        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    
@app.get("/api/rooms/{room_id}/day")
def api_room_day(room_id: str, date_str: str = Query(..., description="YYYY-MM-DD")):
    cfg = load_config_v2()
    room = get_room_or_404(cfg, room_id)

    configured_mac = (room.get("mac") or "").strip()
    if not configured_mac:
        return {"status": "not_configured", "message": f"Room '{room_id}' has no hygrometer configured yet.", "rows": []}

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except:
        raise HTTPException(status_code=400, detail="Use date_str=YYYY-MM-DD")

    start = f"{date_str}T00:00:00Z"
    end   = f"{date_str}T23:59:59Z"

    conn = get_db()
    try:
        cur = conn.execute(
            """SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv
               FROM readings
               WHERE room_id=? AND ts_utc BETWEEN ? AND ?
               ORDER BY epoch ASC""",
            (room_id, start, end)
        )
        rows = [{
            "ts_utc": r[0],
            "epoch": r[1],
            "temp_c": r[2],
            "humidity_pct": r[3],
            "battery_mv": r[4],
        } for r in cur.fetchall()]
    finally:
        conn.close()

    return {"status": "ok", "message": None, "room_id": room_id, "date_str": date_str, "rows": rows}

@app.post("/api/ingest/reading")
def api_ingest_reading(req: IngestReadingReq):
    cfg = load_config_v2()
    room_id = room_id_for_mac(cfg, req.mac)
    if not room_id:
        raise HTTPException(status_code=400, detail=f"MAC not mapped to any enabled room: {req.mac}")

    ts = (req.ts_utc or "").strip()
    if ts.endswith("+00:00"):
        ts = ts[:-6] + "Z"

    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO readings(room_id, ts_utc, epoch, temp_c, humidity_pct, battery_mv) VALUES (?,?,?,?,?,?)",
            (room_id, ts, int(req.epoch), req.temp_c, req.humidity_pct, req.battery_mv)
        )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "room_id": room_id}

@app.get("/api/reports")
def list_reports():
    base = Path(REPORTS_DIR)
    base.mkdir(parents=True, exist_ok=True)

    files = sorted(
        list(base.glob("**/*.zip")) +
        list(base.glob("**/*.pdf"))
    )

    rel = [str(p.relative_to(base)) for p in files]
    rel.sort(reverse=True)

    return {"reports": rel}


@app.get("/api/reports/download")
def download_report(path: str):
    base = Path(REPORTS_DIR).resolve()
    fp = (base / path).resolve()

    if not str(fp).startswith(str(base) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not fp.exists():
        raise HTTPException(status_code=404, detail="Not found")

    media = "application/pdf" if fp.suffix == ".pdf" else "application/zip"
    return FileResponse(str(fp), filename=fp.name, media_type=media)



@app.get("/api/setup/selected")
def setup_selected():
    cfg = _load_setup_cfg()
    mac = (cfg.get("device_mac") or "").strip()
    name = (cfg.get("device_name") or "").strip()

    return {"mac": mac or None, "name": name or None}

@app.post("/api/setup/select")
def setup_select(req: SelectDeviceReq):
    mac = (req.mac or "").strip().upper()
    if not mac:
        raise HTTPException(status_code=400, detail="mac is required")

    cfg = load_config_v2()
    rooms = cfg.get("rooms") or []

    # Find default room, else create it
    default = None
    for r in rooms:
        if (r.get("id") or "").strip() == "default":
            default = r
            break
    if default is None:
        default = {"id": "default", "label": "Default", "mac": "", "name": None, "enabled": True}
        rooms.insert(0, default)

    default["mac"] = mac
    default["name"] = (req.name or "").strip() or None
    cfg["rooms"] = rooms

    _save_setup_cfg(cfg)

    return {"ok": True, "device": {"mac": default["mac"], "name": default["name"]}}


def get_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)

    # --- Detect existing schema ---
    conn.execute("CREATE TABLE IF NOT EXISTS __meta(dummy INTEGER)")
    cols = [row[1] for row in conn.execute("PRAGMA table_info(readings)").fetchall()]  # [cid, name, type, ...]
    has_readings = len(cols) > 0
    has_room_id = "room_id" in cols

    # --- If readings exists but has no room_id, migrate ---
    if has_readings and not has_room_id:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS readings_v2 (
                room_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                temp_c REAL,
                humidity_pct REAL,
                battery_mv INTEGER,
                PRIMARY KEY (room_id, epoch)
            )
        """)
        # Copy old data into default room
        conn.execute("""
            INSERT OR REPLACE INTO readings_v2(room_id, ts_utc, epoch, temp_c, humidity_pct, battery_mv)
            SELECT 'default', ts_utc, epoch, temp_c, humidity_pct, battery_mv
            FROM readings
        """)
        conn.execute("DROP TABLE readings")
        conn.execute("ALTER TABLE readings_v2 RENAME TO readings")
        conn.commit()

    # --- Ensure v2 schema exists (fresh installs land here) ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            room_id TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            temp_c REAL,
            humidity_pct REAL,
            battery_mv INTEGER,
            PRIMARY KEY (room_id, epoch)
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_room_epoch ON readings(room_id, epoch)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_room_ts ON readings(room_id, ts_utc)")
    return conn

def import_csv_bytes(raw: bytes, conn: sqlite3.Connection,  room_id: str = "default") -> int:
    """
    Robust importer for current.csv.

    Accepts either:
      A) 5-col canonical format:
         timestamp_iso,epoch,temp_c,humidity_pct,`battery_mv

      B) 4-col friendly format:
         timestamp,temperature_c,humidity_percent,battery_mv
         (epoch is derived from timestamp)

    Also tolerates:
      - timestamps ending with 'Z' or '+00:00'
      - extra whitespace
      - ragged rows
      - header mismatch (e.g., 4 headers but 5 values)
    """
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))

    try:
        header = next(reader)
    except StopIteration:
        return 0

    # Normalize header (we don't fully trust it for row parsing)
    header_norm = [h.strip().lower() for h in header]

    def to_epoch(ts: str) -> int:
        s = (ts or "").strip()
        if not s:
            raise ValueError("empty timestamp")
        # Accept Zulu time
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp())

    def store_ts_z(ts: str) -> str:
        """Store timestamps consistently with trailing Z for /api/day BETWEEN logic."""
        s = (ts or "").strip()
        if not s:
            raise ValueError("empty timestamp")
        if s.endswith("+00:00"):
            return s[:-6] + "Z"
        return s

    def float_or_none(v: str):
        v = (v or "").strip()
        if v == "":
            return None
        try:
            return float(v)
        except:
            return None

    inserted = 0

    with conn:
        for row in reader:
            if not row:
                continue

            # Strip cells
            vals = [("" if c is None else str(c).strip()) for c in row]

            # --- Canonical row interpretation by length (header may lie) ---
            ts_raw = ""
            ep_raw = ""
            temp_raw = ""
            hum_raw = ""
            batt_raw = ""

            if len(vals) >= 5:
                # Prefer 5-col: ts, epoch, temp, hum, batt
                ts_raw, ep_raw, temp_raw, hum_raw, batt_raw = vals[0], vals[1], vals[2], vals[3], vals[4]

                # If the second column is NOT epoch-like, fallback to 4-col interpretation
                # (this helps when a row has extra columns unrelated to epoch)
                try:
                    ep_num = float(ep_raw) if ep_raw != "" else None
                except:
                    ep_num = None

                if ep_num is None or ep_num < 10_000_000:
                    # Treat as 4-col: ts, temp, hum, batt (ignore extras)
                    ts_raw, temp_raw, hum_raw, batt_raw = vals[0], vals[1], vals[2], vals[3]
                    ep_raw = ""

            elif len(vals) == 4:
                # 4-col: ts, temp, hum, batt
                ts_raw, temp_raw, hum_raw, batt_raw = vals[0], vals[1], vals[2], vals[3]
                ep_raw = ""
            else:
                # Not enough columns
                continue

            ts_raw = (ts_raw or "").strip()
            if not ts_raw:
                continue

            # epoch: take from column if valid, else derive from ts
            epoch = None
            if ep_raw:
                try:
                    epoch = int(float(ep_raw))
                except:
                    epoch = None

            if epoch is None:
                try:
                    epoch = to_epoch(ts_raw)
                except:
                    continue

            temp = float_or_none(temp_raw)
            hum  = float_or_none(hum_raw)
            batt = float_or_none(batt_raw)
            batt_int = int(batt) if batt is not None else None

            # Store timestamp consistently for /api/day BETWEEN string comparison
            try:
                ts_store = store_ts_z(ts_raw)
            except:
                continue

            conn.execute(
                "INSERT OR REPLACE INTO readings(room_id, ts_utc, epoch, temp_c, humidity_pct, battery_mv) VALUES (?,?,?,?,?,?)",
                (room_id, ts_store, epoch, temp, hum, batt_int)
            )
            inserted += 1

    return inserted


@app.get("/api/insights/latest")
def api_insights_latest():
    if not os.path.exists(INSIGHTS_PATH):
        return {"ok": False, "detail": "No insights yet"}
    with open(INSIGHTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/upload")
async def upload_csv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    raw = await file.read()
    # Save uploaded file for audit/debug
    save_path = os.path.join("static", "uploads", f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
    with open(save_path, "wb") as f:
        f.write(raw)

    # Parse CSV
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))

    try:
        header = next(reader)
    except StopIteration:
        raise HTTPException(status_code=400, detail="Empty CSV")

    header = [h.strip().lower() for h in header]
    # Accept both old (4-col) and new (5-col) formats
    # old: timestamp_iso, epoch, temp_c, humidity_pct
    # new: timestamp_iso, epoch, temp_c, humidity_pct, battery_mv
    # tolerate extra columns by slicing to first 5
    EXPECTED_MIN = ["timestamp_iso", "epoch", "temp_c", "humidity_pct"]
    if not all(h in header for h in [EXPECTED_MIN[0], EXPECTED_MIN[1]]):
        raise HTTPException(status_code=400, detail=f"Header must include at least: {EXPECTED_MIN[:2]}")

    # normalize column indices
    def idx(name: str) -> Optional[int]:
        return header.index(name) if name in header else None

    i_ts = idx("timestamp_iso")
    i_ep = idx("epoch")
    i_t  = idx("temp_c")
    i_h  = idx("humidity_pct")
    i_b  = idx("battery_mv") if "battery_mv" in header else None

    conn = get_db()
    inserted = 0
    with conn:
        for row in reader:
            if not row: 
                continue
            # guard against ragged rows
            # pad to length at least 5
            row = list(row) + [""] * (5 - len(row))
            ts = (row[i_ts] or "").strip()
            ep_raw = (row[i_ep] or "").strip()
            if not ts or not ep_raw:
                continue
            try:
                # Accept int epoch or float string epoch
                epoch = int(float(ep_raw))
            except:
                continue

            def f_or_none(i):
                if i is None: return None
                v = (row[i] or "").strip()
                if v == "": return None
                try:
                    return float(v)
                except:
                    return None

            temp = f_or_none(i_t)
            hum  = f_or_none(i_h)
            batt = f_or_none(i_b)
            batt_int = int(batt) if batt is not None else None

            conn.execute(
                "INSERT OR REPLACE INTO readings(room_id, ts_utc, epoch, temp_c, humidity_pct, battery_mv) VALUES (?,?,?,?,?,?)",
                ("default", ts, epoch, temp, hum, batt_int)
            )
            inserted += 1

    return {"ok": True, "inserted": inserted, "saved_as": save_path}

@app.post("/api/import-current")
def import_current_csv():
    csv_path = os.path.join(DATA_DIR, "current.csv")  # /data/current.csv
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail=f"Missing {csv_path}")

    with open(csv_path, "rb") as f:
        raw = f.read()

    cfg = load_config_v2()
    primary = get_primary_room(cfg)
    room_id = (primary.get("id") or "default")

    conn = get_db()
    inserted = import_csv_bytes(raw, conn, room_id=room_id)

    return {"ok": True, "inserted": inserted, "source": csv_path, "room_id": room_id}


@app.get("/api/latest")
def api_latest():
    cfg = load_config_v2()
    primary = get_primary_room(cfg)
    room_id = (primary.get("id") or "default")

    configured_mac = (primary.get("mac") or "").strip()

    conn = get_db()
    try:
        row = fetch_latest_row(conn, room_id)
    finally:
        conn.close()

    if not configured_mac:
        return {
            "status": "not_configured",
            "message": "No hygrometer configured yet. Please open Setup and select a device.",
            "age_seconds": None,
            "reading": None,
        }

    if not row:
        return {
            "status": "no_data",
            "message": "No readings found yet. Make sure the collector is running.",
            "age_seconds": None,
            "reading": None,
        }

    keys = ["ts_utc", "epoch", "temp_c", "humidity_pct", "battery_mv"]
    reading = dict(zip(keys, row))

    age_seconds = calc_age_seconds(reading.get("epoch"))
    stale_seconds = int(os.getenv("STALE_SECONDS", "600"))

    if age_seconds is None:
        return {
            "status": "stale",
            "message": "Cannot determine data age. Collector may not be updating.",
            "age_seconds": None,
            "reading": reading,
        }

    if age_seconds > stale_seconds:
        return {
            "status": "stale",
            "message": f"Last update was {age_seconds} seconds ago. Collector may not be receiving the device {configured_mac}.",
            "age_seconds": age_seconds,
            "reading": reading,
        }

    return {
        "status": "ok",
        "message": None,
        "age_seconds": age_seconds,
        "reading": reading,
    }
    
@app.get("/api/day")
def api_day(date_str: str):
    cfg = load_config_v2()
    primary = get_primary_room(cfg)
    room_id = (primary.get("id") or "default")

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except:
        raise HTTPException(status_code=400, detail="Use date=YYYY-MM-DD")

    start = f"{date_str}T00:00:00Z"
    end   = f"{date_str}T23:59:59Z"

    conn = get_db()
    try:
        cur = conn.execute(
            """SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv
               FROM readings
               WHERE room_id=? AND ts_utc BETWEEN ? AND ?
               ORDER BY epoch ASC""",
            (room_id, start, end)
        )
        rows = [{
            "ts_utc": r[0],
            "epoch": r[1],
            "temp_c": r[2],
            "humidity_pct": r[3],
            "battery_mv": r[4],
        } for r in cur.fetchall()]
    finally:
        conn.close()

    return {"rows": rows}