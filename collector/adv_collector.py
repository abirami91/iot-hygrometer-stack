import os, time, csv, pathlib, asyncio, struct, binascii
from datetime import datetime
from bleak import BleakScanner
import json

CONFIG_PATH = os.getenv("SETUP_CONFIG_PATH", "/data/config.json")
FE95 = "0000fe95-0000-1000-8000-00805f9b34fb"
OUTPUT = os.getenv("OUTPUT", "/data/current.csv")
INTERVAL = int(os.getenv("INTERVAL_SECONDS", "600"))
MIN_RSSI = int(os.getenv("MIN_RSSI", "-120"))
selected_mac = None
last_cfg_check = 0.0
CFG_REFRESH_SECS = int(os.getenv("CFG_REFRESH_SECS", "5"))


last = {"temp_c": None, "humidity_pct": None}
last_seen = 0.0
last_written = 0.0

def get_selected_mac():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        mac = (cfg.get("device_mac") or "").strip().upper()
        return mac or None
    except Exception:
        return None


def ensure_csv(p):
    pp = pathlib.Path(p)
    pp.parent.mkdir(parents=True, exist_ok=True)
    if not pp.exists():
        with pp.open("w", newline="") as f:
            csv.writer(f).writerow(["timestamp_iso", "epoch", "temp_c", "humidity_pct"])

def parse_and_debug(sd: bytes):
    # MiBeacon (simplified): [fc(2)][devId(1)][cnt(1)][len(1)] + events...
    info = {}
    if len(sd) < 5:
        return None, info
    fc = int.from_bytes(sd[0:2], "little")
    enc = bool(fc & (1 << 3))
    dev_id = sd[2]
    counter = sd[3]
    payload_len = sd[4]
    info["fc"] = hex(fc)
    info["enc"] = enc
    info["dev_id"] = hex(dev_id)
    info["cnt"] = counter
    info["len"] = payload_len
    info["hex"] = binascii.hexlify(sd).decode()

    if enc:
        return None, info

    pos = 5
    out = {}
    events = []
    while pos + 3 <= len(sd):
        eid = int.from_bytes(sd[pos:pos+2], "little"); pos += 2
        elen = sd[pos]; pos += 1
        if pos + elen > len(sd):
            break
        payload = sd[pos:pos+elen]; pos += elen
        events.append({"id": hex(eid), "len": elen, "hex": binascii.hexlify(payload).decode()})
        if eid == 0x1004 and elen >= 2:      # Temperature
            out["temp_c"] = int.from_bytes(payload[:2], "little", signed=True) / 100.0
        elif eid == 0x1006 and elen >= 2:    # Humidity
            out["humidity_pct"] = int.from_bytes(payload[:2], "little") / 100.0
        elif eid == 0x100D and elen >= 4:    # Temp + Humidity
            out["temp_c"] = int.from_bytes(payload[:2], "little", signed=True) / 100.0
            out["humidity_pct"] = int.from_bytes(payload[2:4], "little") / 100.0
    info["events"] = events
    return (out or None), info


def on_adv(d, adv):
    global last, last_seen
    global selected_mac, last_cfg_check

    # ---- refresh selected device periodically ----
    now = time.time()
    if now - last_cfg_check >= CFG_REFRESH_SECS:
        selected_mac = get_selected_mac()
        last_cfg_check = now

    # ---- no device selected yet ----
    if not selected_mac:
        return

    # ---- ignore other BLE devices early ----
    if d.address.strip().upper() != selected_mac:
        return

    # ---- existing logic continues ----
    if adv.rssi is not None and adv.rssi < MIN_RSSI:
        return

    sd = adv.service_data.get(FE95)
    if not sd:
        return

async def main():
    global last_written
    ensure_csv(OUTPUT)
    scanner = BleakScanner(detection_callback=on_adv, scanning_mode="active")
    await scanner.start()
    print("[INFO] Listening (active scan, debug)…", flush=True)
    try:
        while True:
            now = time.time()
            if (now - last_written) >= INTERVAL and last_seen > 0 and (now - last_seen) < (INTERVAL * 3):
                with open(OUTPUT, "a", newline="") as f:
                    csv.writer(f).writerow([
                        datetime.utcfromtimestamp(now).isoformat() + "Z",
                        f"{now:.0f}",
                        f"{last.get('temp_c'):.2f}" if last.get('temp_c') is not None else "",
                        f"{last.get('humidity_pct'):.2f}" if last.get('humidity_pct') is not None else "",
                    ])
                last_written = now
                print(f"[LOG] wrote CSV at {datetime.fromtimestamp(now)}", flush=True)
            await asyncio.sleep(1)
    finally:
        await scanner.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
