import csv
import io
import os
import sqlite3
from datetime import datetime, date
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

DB_PATH = os.path.join("data", "hygro.db")
os.makedirs("data", exist_ok=True)
os.makedirs("static/uploads", exist_ok=True)
CSV_CURRENT = os.path.join("data", "current.csv")  # because /app/data is mounted to ./data


app = FastAPI(title="Hygrometer Cloud")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def get_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            ts_utc TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            temp_c REAL,
            humidity_pct REAL,
            battery_mv INTEGER,
            PRIMARY KEY (ts_utc, epoch)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_epoch ON readings(epoch)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON readings(ts_utc)")
    return conn

def import_csv_bytes(raw: bytes, conn: sqlite3.Connection) -> int:
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))

    try:
        header = next(reader)
    except StopIteration:
        return 0

    header = [h.strip().lower() for h in header]

    def idx(name: str) -> Optional[int]:
        return header.index(name) if name in header else None

    i_ts = idx("timestamp_iso")
    i_ep = idx("epoch")
    i_t  = idx("temp_c")
    i_h  = idx("humidity_pct")
    i_b  = idx("battery_mv") if "battery_mv" in header else None

    if i_ts is None or i_ep is None:
        return 0

    inserted = 0
    with conn:
        for row in reader:
            if not row:
                continue
            row = list(row) + [""] * (5 - len(row))

            ts = (row[i_ts] or "").strip()
            ep_raw = (row[i_ep] or "").strip()
            if not ts or not ep_raw:
                continue

            try:
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
                "INSERT OR REPLACE INTO readings(ts_utc, epoch, temp_c, humidity_pct, battery_mv) VALUES (?,?,?,?,?)",
                (ts, epoch, temp, hum, batt_int)
            )
            inserted += 1

    return inserted



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
                "INSERT OR REPLACE INTO readings(ts_utc, epoch, temp_c, humidity_pct, battery_mv) VALUES (?,?,?,?,?)",
                (ts, epoch, temp, hum, batt_int)
            )
            inserted += 1

    return {"ok": True, "inserted": inserted, "saved_as": save_path}

@app.post("/api/import-current")
def import_current_csv():
    csv_path = os.path.join("data", "current.csv")  # inside container
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail=f"Missing {csv_path}")

    with open(csv_path, "rb") as f:
        raw = f.read()

    conn = get_db()
    inserted = import_csv_bytes(raw, conn)

    return {"ok": True, "inserted": inserted, "source": csv_path}


@app.get("/api/latest")
def api_latest():
    conn = get_db()
    cur = conn.execute(
        "SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv FROM readings ORDER BY epoch DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return {"reading": None}
    keys = ["ts_utc", "epoch", "temp_c", "humidity_pct", "battery_mv"]
    return {"reading": dict(zip(keys, row))}


@app.get("/api/day")
def api_day(date_str: str):
    """
    date_str: YYYY-MM-DD (local date you want to view)
    We filter by the UTC timestamp strings' date (approx; your CSV uses ISO Z times).
    """
    try:
        # keep as string; CSV uses ISO with Z
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except:
        raise HTTPException(status_code=400, detail="Use date=YYYY-MM-DD")

    start = f"{date_str}T00:00:00Z"
    end   = f"{date_str}T23:59:59Z"

    conn = get_db()
    cur = conn.execute(
        """SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv
           FROM readings
           WHERE ts_utc BETWEEN ? AND ?
           ORDER BY epoch ASC""",
        (start, end)
    )
    rows = [{
        "ts_utc": r[0],
        "epoch": r[1],
        "temp_c": r[2],
        "humidity_pct": r[3],
        "battery_mv": r[4],
    } for r in cur.fetchall()]
    return {"rows": rows}
