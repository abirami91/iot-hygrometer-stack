import os, json, csv, zipfile, sqlite3, smtplib
from datetime import datetime
from email.message import EmailMessage

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors


DATA_DIR = os.getenv("DATA_DIR", "/data")
DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "hygro.db"))
REPORTS_DIR = os.getenv("REPORTS_DIR", os.path.join(DATA_DIR, "reports"))

HUMIDITY_WARN  = float(os.getenv("HUMIDITY_WARN", "60"))
HUMIDITY_ALERT = float(os.getenv("HUMIDITY_ALERT", "65"))

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_TO   = os.getenv("SMTP_TO", "")  # comma-separated
SMTP_TLS  = os.getenv("SMTP_TLS", "1") == "1"


def iso_day_bounds(date_str: str):
    # DB stores ts_utc like 2026-02-10T19:50:08Z
    start = f"{date_str}T00:00:00Z"
    end   = f"{date_str}T23:59:59Z"
    return start, end


def load_rows(date_str: str):
    start, end = iso_day_bounds(date_str)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """SELECT ts_utc, epoch, temp_c, humidity_pct, battery_mv
           FROM readings
           WHERE ts_utc BETWEEN ? AND ?
           ORDER BY epoch ASC""",
        (start, end),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {}
    return {"min": min(xs), "max": max(xs), "avg": sum(xs) / len(xs)}


def hours_above(rows, threshold):
    """
    Approx time-above using epoch deltas between consecutive points.
    For 20-min sampling this is good enough.
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
            # cap insane gaps (e.g., device offline)
            dt = min(dt, 60 * 60 * 3)  # max 3h per step
            total += dt
    return round(total / 3600.0, 2)


def generate_pdf(date_str: str, rows, summary, pdf_path: str):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(pdf_path, pagesize=A4, title=f"Hygrometer Report {date_str}")

    story = []
    story.append(Paragraph(f"<b>Hygrometer Daily Report</b> — {date_str}", styles["Title"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph(f"Samples: <b>{summary.get('rows', 0)}</b>", styles["Normal"]))
    story.append(Paragraph(f"Generated (UTC): {summary.get('generated_utc', '—')}", styles["Normal"]))
    story.append(Spacer(1, 10))

    t = summary.get("temp_c", {})
    h = summary.get("humidity_pct", {})
    b = summary.get("battery_mv", {})

    def fmt_stat(d, unit="", digits=2):
        if not d:
            return "—"
        return (
            f"min={d.get('min'):.{digits}f}{unit}, "
            f"max={d.get('max'):.{digits}f}{unit}, "
            f"avg={d.get('avg'):.{digits}f}{unit}"
        )

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
        story.append(Paragraph(f"<b>Battery</b>: —", styles["Normal"]))

    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            f"Hours humidity ≥ {HUMIDITY_WARN}%: <b>{summary.get('hours_humidity_above_warn', 0)}</b> | "
            f"≥ {HUMIDITY_ALERT}%: <b>{summary.get('hours_humidity_above_alert', 0)}</b>",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 14))

    # Table of readings (limit to keep PDF readable; change if you want all)
    rows_for_table = rows[-200:] if rows else []

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

    doc.build(story)


def generate_report(date_str: str) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    day_dir = os.path.join(REPORTS_DIR, date_str)
    os.makedirs(day_dir, exist_ok=True)

    rows = load_rows(date_str)

    if not rows:
        summary = {
            "date": date_str,
            "rows": 0,
            "detail": "No data for this day",
            "generated_utc": datetime.utcnow().isoformat() + "Z",
            "thresholds": {"warn": HUMIDITY_WARN, "alert": HUMIDITY_ALERT},
            "hours_humidity_above_warn": 0,
            "hours_humidity_above_alert": 0,
            "temp_c": {},
            "humidity_pct": {},
            "battery_mv": {},
        }

        pdf_path = os.path.join(day_dir, f"report_{date_str}.pdf")
        generate_pdf(date_str, rows, summary, pdf_path)

        zip_path = os.path.join(day_dir, f"report_{date_str}.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("summary.json", json.dumps(summary, indent=2))
            z.write(pdf_path, arcname=os.path.basename(pdf_path))
        return zip_path

    temps = [r[2] for r in rows]
    hums  = [r[3] for r in rows]
    batts = [r[4] for r in rows]

    summary = {
        "date": date_str,
        "rows": len(rows),
        "temp_c": stats(temps),
        "humidity_pct": stats(hums),
        "battery_mv": stats(batts),
        "hours_humidity_above_warn": hours_above(rows, HUMIDITY_WARN),
        "hours_humidity_above_alert": hours_above(rows, HUMIDITY_ALERT),
        "thresholds": {"warn": HUMIDITY_WARN, "alert": HUMIDITY_ALERT},
        "generated_utc": datetime.utcnow().isoformat() + "Z",
    }

    # Build CSV text
    csv_lines = []
    csv_lines.append("timestamp_iso,epoch,temp_c,humidity_pct,battery_mv")
    for ts, ep, t, h, b in rows:
        csv_lines.append(
            ",".join(
                [
                    str(ts),
                    str(ep),
                    "" if t is None else str(t),
                    "" if h is None else str(h),
                    "" if b is None else str(b),
                ]
            )
        )
    csv_text = "\n".join(csv_lines) + "\n"

    # Generate PDF file
    pdf_path = os.path.join(day_dir, f"report_{date_str}.pdf")
    generate_pdf(date_str, rows, summary, pdf_path)

    # Zip everything
    zip_path = os.path.join(day_dir, f"report_{date_str}.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("summary.json", json.dumps(summary, indent=2))
        z.writestr("data.csv", csv_text)
        z.write(pdf_path, arcname=os.path.basename(pdf_path))

    return zip_path


def send_email(date_str: str, zip_path: str):
    if not (SMTP_HOST and SMTP_TO and SMTP_FROM):
        print("[INFO] SMTP not configured; skipping email.")
        return

    recipients = [x.strip() for x in SMTP_TO.split(",") if x.strip()]

    msg = EmailMessage()
    msg["Subject"] = f"Hygrometer nightly report — {date_str}"
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(recipients)

    # Load summary from zip for email body
    summary = None
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            if "summary.json" in z.namelist():
                summary = json.loads(z.read("summary.json").decode("utf-8"))
    except Exception:
        summary = None

    body = [f"Daily report for {date_str}", ""]
    if summary:
        body.append(f"Rows: {summary.get('rows')}")
        if summary.get("rows", 0) > 0:
            t = summary.get("temp_c", {})
            h = summary.get("humidity_pct", {})
            b = summary.get("battery_mv", {})
            body += [
                f"Temp (°C): min={t.get('min'):.2f} max={t.get('max'):.2f} avg={t.get('avg'):.2f}" if t else "Temp: —",
                f"Hum  (%):  min={h.get('min'):.2f} max={h.get('max'):.2f} avg={h.get('avg'):.2f}" if h else "Hum: —",
                f"Batt (mV): min={b.get('min')} max={b.get('max')} avg={int(b.get('avg'))}" if b else "Batt: —",
                f"Hours > warn({HUMIDITY_WARN}%):  {summary.get('hours_humidity_above_warn')}",
                f"Hours > alert({HUMIDITY_ALERT}%): {summary.get('hours_humidity_above_alert')}",
            ]
    else:
        body.append("Summary could not be loaded from the report zip.")

    msg.set_content("\n".join(body))

    # ---- attach ZIP ----
    with open(zip_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="zip",
            filename=os.path.basename(zip_path),
        )

    # ---- attach PDF (same folder, same base name) ----
    pdf_path = zip_path.replace(".zip", ".pdf")
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="pdf",
                filename=os.path.basename(pdf_path),
            )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        if SMTP_TLS:
            s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

    print(f"[OK] emailed report to {recipients}")


def main():
    # default: report for "today" in local time (container TZ), typically run at night
    date_str = os.getenv("REPORT_DATE", "")
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    zip_path = generate_report(date_str)
    print(f"[OK] generated {zip_path}")

    if os.getenv("SEND_EMAIL", "1") == "1":
        send_email(date_str, zip_path)


if __name__ == "__main__":
    main()
