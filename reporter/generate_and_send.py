import os
import json
import zipfile
import sqlite3
import smtplib
from datetime import datetime
from email.message import EmailMessage

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors


DATA_DIR = os.getenv("DATA_DIR", "/data")
DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "hygro.db"))
REPORTS_DIR = os.getenv("REPORTS_DIR", os.path.join(DATA_DIR, "reports"))
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

HUMIDITY_WARN = float(os.getenv("HUMIDITY_WARN", "60"))
HUMIDITY_ALERT = float(os.getenv("HUMIDITY_ALERT", "65"))


def load_email_settings():
    email_cfg = {}

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            email_cfg = cfg.get("email", {}) or {}
        except Exception as e:
            print(f"[WARN] failed to read {CONFIG_PATH}: {e}")

    smtp_user = str(email_cfg.get("smtp_user", "")).strip() or os.getenv("SMTP_USER", "")
    smtp_host = str(email_cfg.get("smtp_host", "")).strip() or os.getenv("SMTP_HOST", "")
    smtp_pass = str(email_cfg.get("smtp_pass", "")).strip() or os.getenv("SMTP_PASS", "")
    smtp_from = str(email_cfg.get("mail_from", "")).strip() or os.getenv("SMTP_FROM", smtp_user)
    smtp_to = str(email_cfg.get("mail_to", "")).strip() or os.getenv("SMTP_TO", "")

    smtp_port_raw = email_cfg.get("smtp_port", None)
    if smtp_port_raw in (None, ""):
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
    else:
        smtp_port = int(smtp_port_raw)

    smtp_tls_raw = email_cfg.get("smtp_tls", None)
    if smtp_tls_raw is None:
        smtp_tls = os.getenv("SMTP_TLS", "1") == "1"
    else:
        smtp_tls = bool(smtp_tls_raw)

    enabled = email_cfg.get("enabled", None)
    if enabled is None:
        enabled = bool(smtp_host and smtp_to and smtp_from)

    return {
        "enabled": bool(enabled),
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_pass": smtp_pass,
        "smtp_from": smtp_from,
        "smtp_to": smtp_to,
        "smtp_tls": smtp_tls,
    }


def iso_day_bounds(date_str: str):
    start = f"{date_str}T00:00:00Z"
    end = f"{date_str}T23:59:59Z"
    return start, end


def load_rooms():
    if not os.path.exists(CONFIG_PATH):
        return []

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception as e:
        print(f"[WARN] failed to read {CONFIG_PATH}: {e}")
        return []

    rooms = []
    for r in (cfg.get("rooms") or []):
        if not r.get("enabled", True):
            continue

        room_id = (r.get("id") or "").strip()
        if not room_id:
            continue

        rooms.append(
            {
                "id": room_id,
                "label": (r.get("label") or "").strip() or room_id,
                "mac": (r.get("mac") or "").strip().upper(),
                "name": (r.get("name") or "").strip(),
                "enabled": bool(r.get("enabled", True)),
            }
        )

    return rooms


def has_room_id_column():
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(readings)")
        cols = cur.fetchall()
        col_names = {c[1] for c in cols}
        return "room_id" in col_names
    finally:
        conn.close()


def load_rows_for_room(date_str: str, room_id: str):
    start, end = iso_day_bounds(date_str)

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()

        if has_room_id_column():
            cur.execute(
                """
                SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv
                FROM readings
                WHERE room_id = ?
                  AND ts_utc >= ?
                  AND ts_utc <= ?
                ORDER BY epoch ASC
                """,
                (room_id, start, end),
            )
        else:
            if room_id != "default":
                return []
            cur.execute(
                """
                SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv
                FROM readings
                WHERE ts_utc >= ?
                  AND ts_utc <= ?
                ORDER BY epoch ASC
                """,
                (start, end),
            )

        return cur.fetchall()
    finally:
        conn.close()


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {}
    return {
        "min": min(xs),
        "max": max(xs),
        "avg": sum(xs) / len(xs),
    }


def hours_above(rows, threshold):
    """
    Approximate time-above using epoch deltas between consecutive points.
    For 20-min sampling this is usually good enough.
    """
    if len(rows) < 2:
        return 0.0

    total = 0
    for i in range(len(rows) - 1):
        _ts, ep, _t, h, _b = rows[i]
        ep2 = rows[i + 1][1]

        if h is None:
            continue

        if float(h) >= threshold:
            dt = max(0, int(ep2) - int(ep))
            dt = min(dt, 60 * 60 * 3)  # cap at 3h to avoid huge offline gaps
            total += dt

    return round(total / 3600.0, 2)


def build_room_summary(room: dict, rows):
    temps = [r[2] for r in rows if r[2] is not None]
    hums = [r[3] for r in rows if r[3] is not None]
    batts = [r[4] for r in rows if r[4] is not None]

    latest = rows[-1] if rows else None

    return {
        "room_id": room["id"],
        "label": room["label"],
        "mac": room.get("mac", ""),
        "name": room.get("name", ""),
        "rows": len(rows),
        "latest": {
            "ts_utc": latest[0] if latest else None,
            "epoch": latest[1] if latest else None,
            "temp_c": latest[2] if latest else None,
            "humidity_pct": latest[3] if latest else None,
            "battery_mv": latest[4] if latest else None,
        },
        "temp_c": stats(temps),
        "humidity_pct": stats(hums),
        "battery_mv": stats(batts),
        "hours_humidity_above_warn": hours_above(rows, HUMIDITY_WARN),
        "hours_humidity_above_alert": hours_above(rows, HUMIDITY_ALERT),
        "table_rows": rows,
    }


def fmt_stat(d, unit="", digits=2):
    if not d:
        return "—"
    return (
        f"min={d.get('min'):.{digits}f}{unit}, "
        f"max={d.get('max'):.{digits}f}{unit}, "
        f"avg={d.get('avg'):.{digits}f}{unit}"
    )


def generate_pdf(date_str: str, room_reports, pdf_path: str):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(pdf_path, pagesize=A4, title=f"Hygrometer Report {date_str}")

    story = []
    generated_utc = datetime.utcnow().isoformat() + "Z"

    story.append(Paragraph(f"<b>Hygrometer Daily Report</b> — {date_str}", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Generated (UTC): {generated_utc}", styles["Normal"]))
    story.append(Paragraph(f"Rooms included: <b>{len(room_reports)}</b>", styles["Normal"]))
    story.append(Paragraph(f"Humidity warn threshold: <b>{HUMIDITY_WARN}%</b>", styles["Normal"]))
    story.append(Paragraph(f"Humidity alert threshold: <b>{HUMIDITY_ALERT}%</b>", styles["Normal"]))
    story.append(Spacer(1, 16))

    if not room_reports:
        story.append(Paragraph("No enabled rooms configured.", styles["Normal"]))
    else:
        for idx, summary in enumerate(room_reports, start=1):
            if idx > 1:
                story.append(PageBreak())

            story.append(
                Paragraph(
                    f"<b>Room {idx}: {summary.get('label', '—')}</b>",
                    styles["Heading2"],
                )
            )
            story.append(Spacer(1, 8))

            if summary.get("mac"):
                story.append(Paragraph(f"MAC: {summary['mac']}", styles["Normal"]))
            if summary.get("name"):
                story.append(Paragraph(f"Device: {summary['name']}", styles["Normal"]))

            story.append(Paragraph(f"Samples: <b>{summary.get('rows', 0)}</b>", styles["Normal"]))

            latest = summary.get("latest") or {}
            if latest.get("ts_utc"):
                latest_temp = "—" if latest.get("temp_c") is None else f"{float(latest['temp_c']):.2f}"
                latest_hum = "—" if latest.get("humidity_pct") is None else f"{float(latest['humidity_pct']):.2f}"
                latest_batt = "—" if latest.get("battery_mv") is None else str(latest["battery_mv"])
                story.append(
                    Paragraph(
                        f"Latest reading: {latest['ts_utc']} | "
                        f"T={latest_temp} °C, H={latest_hum} %, B={latest_batt} mV",
                        styles["Normal"],
                    )
                )

            story.append(Spacer(1, 10))

            t = summary.get("temp_c", {})
            h = summary.get("humidity_pct", {})
            b = summary.get("battery_mv", {})

            story.append(Paragraph(f"<b>Temperature</b>: {fmt_stat(t, '°C', 2)}", styles["Normal"]))
            story.append(Paragraph(f"<b>Humidity</b>: {fmt_stat(h, '%', 2)}", styles["Normal"]))

            if b:
                story.append(
                    Paragraph(
                        f"<b>Battery</b>: min={b.get('min')}, max={b.get('max')}, avg={int(b.get('avg'))}",
                        styles["Normal"],
                    )
                )
            else:
                story.append(Paragraph("<b>Battery</b>: —", styles["Normal"]))

            story.append(Spacer(1, 8))
            story.append(
                Paragraph(
                    f"Hours humidity ≥ {HUMIDITY_WARN}%: <b>{summary.get('hours_humidity_above_warn', 0)}</b> | "
                    f"≥ {HUMIDITY_ALERT}%: <b>{summary.get('hours_humidity_above_alert', 0)}</b>",
                    styles["Normal"],
                )
            )
            story.append(Spacer(1, 12))

            rows_for_table = (summary.get("table_rows") or [])[-100:]

            if rows_for_table:
                table_data = [["Time (UTC)", "Temp (°C)", "Hum (%)", "Battery (mV)"]]
                for ts, _ep, temp, hum, batt in rows_for_table:
                    table_data.append(
                        [
                            ts,
                            "" if temp is None else f"{float(temp):.2f}",
                            "" if hum is None else f"{float(hum):.2f}",
                            "" if batt is None else str(batt),
                        ]
                    )

                tbl = Table(table_data, repeatRows=1)
                tbl.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                        ]
                    )
                )
                story.append(tbl)
            else:
                story.append(Paragraph("No readings for this room on this day.", styles["Normal"]))

    doc.build(story)


def generate_report(date_str: str) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    day_dir = os.path.join(REPORTS_DIR, date_str)
    os.makedirs(day_dir, exist_ok=True)

    rooms = load_rooms()
    room_reports = []

    for room in rooms:
        rows = load_rows_for_room(date_str, room["id"])
        room_reports.append(build_room_summary(room, rows))

    summary = {
        "date": date_str,
        "generated_utc": datetime.utcnow().isoformat() + "Z",
        "thresholds": {
            "warn": HUMIDITY_WARN,
            "alert": HUMIDITY_ALERT,
        },
        "room_count": len(room_reports),
        "total_rows": sum(r.get("rows", 0) for r in room_reports),
        "rooms": [],
    }

    for room_summary in room_reports:
        summary["rooms"].append(
            {
                "room_id": room_summary["room_id"],
                "label": room_summary["label"],
                "mac": room_summary.get("mac", ""),
                "name": room_summary.get("name", ""),
                "rows": room_summary["rows"],
                "latest": room_summary["latest"],
                "temp_c": room_summary["temp_c"],
                "humidity_pct": room_summary["humidity_pct"],
                "battery_mv": room_summary["battery_mv"],
                "hours_humidity_above_warn": room_summary["hours_humidity_above_warn"],
                "hours_humidity_above_alert": room_summary["hours_humidity_above_alert"],
            }
        )

    pdf_path = os.path.join(day_dir, f"report_{date_str}.pdf")
    generate_pdf(date_str, room_reports, pdf_path)

    csv_lines = ["room_id,room_label,timestamp_iso,epoch,temp_c,humidity_pct,battery_mv"]
    for room_summary in room_reports:
        room_id = room_summary.get("room_id", "")
        room_label = room_summary.get("label", "")
        for ts, ep, t, h, b in room_summary.get("table_rows", []):
            csv_lines.append(
                ",".join(
                    [
                        str(room_id),
                        str(room_label),
                        str(ts),
                        str(ep),
                        "" if t is None else str(t),
                        "" if h is None else str(h),
                        "" if b is None else str(b),
                    ]
                )
            )

    csv_text = "\n".join(csv_lines) + "\n"

    zip_path = os.path.join(day_dir, f"report_{date_str}.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("summary.json", json.dumps(summary, indent=2))
        z.writestr("data.csv", csv_text)
        z.write(pdf_path, arcname=os.path.basename(pdf_path))

    return zip_path


def send_email(date_str: str, zip_path: str):
    email = load_email_settings()

    if not email.get("enabled"):
        print("[INFO] Email disabled; skipping email.")
        return

    smtp_host = email["smtp_host"]
    smtp_port = email["smtp_port"]
    smtp_user = email["smtp_user"]
    smtp_pass = email["smtp_pass"]
    smtp_from = email["smtp_from"]
    smtp_to = email["smtp_to"]
    smtp_tls = email["smtp_tls"]

    if not (smtp_host and smtp_to and smtp_from):
        print("[INFO] SMTP not configured; skipping email.")
        return

    recipients = [x.strip() for x in smtp_to.split(",") if x.strip()]
    if not recipients:
        print("[INFO] No recipients configured; skipping email.")
        return

    summary = None
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            if "summary.json" in z.namelist():
                summary = json.loads(z.read("summary.json").decode("utf-8"))
    except Exception as e:
        print(f"[WARN] failed to read summary from zip: {e}")

    msg = EmailMessage()
    msg["Subject"] = f"Hygrometer nightly report — {date_str}"
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)

    body = [f"Daily hygrometer report for {date_str}", ""]

    if summary:
        body.append(f"Rooms included: {summary.get('room_count', 0)}")
        body.append(f"Total rows: {summary.get('total_rows', 0)}")
        body.append("")

        for room in summary.get("rooms", []):
            body.append(f"Room: {room.get('label', room.get('room_id', '—'))}")
            body.append(f"Rows: {room.get('rows', 0)}")

            t = room.get("temp_c", {})
            h = room.get("humidity_pct", {})
            b = room.get("battery_mv", {})

            body.append(
                f"Temp (°C): min={t.get('min'):.2f} max={t.get('max'):.2f} avg={t.get('avg'):.2f}"
                if t else "Temp: —"
            )
            body.append(
                f"Hum  (%): min={h.get('min'):.2f} max={h.get('max'):.2f} avg={h.get('avg'):.2f}"
                if h else "Hum: —"
            )
            body.append(
                f"Batt (mV): min={b.get('min')} max={b.get('max')} avg={int(b.get('avg'))}"
                if b else "Batt: —"
            )
            body.append(f"Hours > warn({HUMIDITY_WARN}%): {room.get('hours_humidity_above_warn', 0)}")
            body.append(f"Hours > alert({HUMIDITY_ALERT}%): {room.get('hours_humidity_above_alert', 0)}")
            body.append("")
    else:
        body.append("Summary could not be loaded from the report zip.")

    msg.set_content("\n".join(body))

    with open(zip_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="zip",
            filename=os.path.basename(zip_path),
        )

    pdf_path = os.path.join(os.path.dirname(zip_path), f"report_{date_str}.pdf")
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="pdf",
                filename=os.path.basename(pdf_path),
            )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
        if smtp_tls:
            s.starttls()
        if smtp_user:
            s.login(smtp_user, smtp_pass)
        s.send_message(msg)

    print(f"[OK] emailed report to {recipients}")


def main():
    # default: report for "today" in local time (container TZ)
    date_str = os.getenv("REPORT_DATE", "").strip()
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    zip_path = generate_report(date_str)
    print(f"[OK] generated {zip_path}")

    if os.getenv("SEND_EMAIL", "1") == "1":
        send_email(date_str, zip_path)


if __name__ == "__main__":
    main()