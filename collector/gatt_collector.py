import os, csv, time, asyncio, pathlib, struct
from datetime import datetime
from bleak import BleakClient, BleakScanner

import json

CONFIG_PATH = os.getenv("SETUP_CONFIG_PATH", "/data/config.json")

# ---- Configuration (env-overridable) ----
NOTIFY_UUID = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"
DEVICE_MAC = None  # will be loaded from config
OUTPUT      = os.getenv("OUTPUT","/data/current.csv")
INTERVAL    = int(os.getenv("INTERVAL_SECONDS","600"))

# connection behavior
SCAN_TIMEOUT       = float(os.getenv("SCAN_TIMEOUT","25"))   # seconds to wait for adverts
CONNECT_TIMEOUT    = float(os.getenv("CONNECT_TIMEOUT","15"))
NOTIFY_WINDOW_SECS = int(os.getenv("NOTIFY_WINDOW_SECS","60"))  # time to stay connected (persistent mode ignores)
IDLE_BETWEEN_CYCLES= int(os.getenv("IDLE_BETWEEN_CYCLES","8"))  # base sleep if nothing to write
PERSISTENT_NOTIFY  = os.getenv("PERSISTENT_NOTIFY","0") == "1"  # keep connection open and stream
MAX_BACKOFF        = int(os.getenv("MAX_BACKOFF","60"))  # cap backoff

# parsing/scaling
HUMIDITY_SCALE     = float(os.getenv("HUMIDITY_SCALE","1.70"))  # your unit: byte * 1.70 ≈ display %RH
PRINT_RAW          = os.getenv("PRINT_RAW","0") == "1"          # set to 1 if you want raw hex logged

VERSION = "gatt-robust v1"

last = {"temp_c":None, "humidity_pct":None, "battery_mv":None}
last_written = 0.0


def get_selected_mac():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        mac = (cfg.get("device_mac") or "").strip().upper()
        return mac or None
    except Exception:
        return None

def ensure_csv(path):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        with p.open("w", newline="") as f:
            csv.writer(f).writerow(["timestamp_iso","epoch","temp_c","humidity_pct","battery_mv"])

def parse_notify(payload: bytes):
    """
    Observed format on your device:
      temp: int16 (°C * 100)   bytes 0..1
      hum : uint8 (%)         byte  2  (needs scaling factor to match LCD)
      batt: uint16 (mV)       bytes 3..4 (LE)
    """
    if len(payload) >= 5:
        t_raw = int.from_bytes(payload[0:2], "little", signed=True)
        h_raw = payload[2]
        batt  = int.from_bytes(payload[3:5], "little")
        t = t_raw / 100.0
        h = round(h_raw * HUMIDITY_SCALE, 2)
        return t, h, batt
    return None, None, None

async def wait_one_notification(client, timeout_s):
    """
    Subscribe and wait for at least one reading (temp+hum).
    """
    vals = {"temp_c":None, "humidity_pct":None, "battery_mv":None}
    done = asyncio.Event()

    def cb(_handle, data: bytes):
        if PRINT_RAW:
            print(f"[RAW] {data.hex()}", flush=True)
        t, h, mv = parse_notify(data)
        if t is not None: vals["temp_c"] = t
        if h is not None: vals["humidity_pct"] = h
        if mv is not None: vals["battery_mv"] = mv
        # as soon as we have both, mark done
        if vals["temp_c"] is not None and vals["humidity_pct"] is not None:
            done.set()

    await client.start_notify(NOTIFY_UUID, cb)
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass
    finally:
        await client.stop_notify(NOTIFY_UUID)
    return vals

async def connect_once():
    """
    Scan to find the device (avoids Bleak 'not found'), then connect.
    """
    dev = await BleakScanner.find_device_by_address(DEVICE_MAC, timeout=SCAN_TIMEOUT)
    if dev is None:
        raise RuntimeError("Device not advertising (scan timed out)")

    client = BleakClient(dev, timeout=CONNECT_TIMEOUT)
    await client.__aenter__()  # async context enter
    return client

async def persistent_stream():
    """
    Stay connected and stream notifications; reconnect on disconnect.
    """
    backoff = 1
    while True:
        try:
            client = await connect_once()
            print(f"[INFO] connected (persistent) to {DEVICE_MAC}", flush=True)
            backoff = 1  # reset backoff on success
            await client.start_notify(NOTIFY_UUID, lambda *_: None)  # start stream

            # inner loop: read for a long time; write when INTERVAL elapsed
            global last, last_written
            t0 = time.time()
            def cb(_h, data: bytes):
                global last
                if PRINT_RAW:
                    print(f"[RAW] {data.hex()}", flush=True)
                t, h, mv = parse_notify(data)
                if t is not None: last["temp_c"] = t
                if h is not None: last["humidity_pct"] = h
                if mv is not None: last["battery_mv"] = mv
                print(f"[GATT] {DEVICE_MAC} -> T={last['temp_c']:.2f}°C H={last['humidity_pct']:.2f}% "
                      f"(batt={last['battery_mv']}mV)", flush=True)

            await client.stop_notify(NOTIFY_UUID)  # restart with our cb
            await client.start_notify(NOTIFY_UUID, cb)

            while True:
                now = time.time()
                if (now - last_written) >= INTERVAL and (last["temp_c"] is not None or last["humidity_pct"] is not None):
                    with open(OUTPUT,"a",newline="") as f:
                        csv.writer(f).writerow([
                            datetime.utcfromtimestamp(now).isoformat()+"Z",
                            f"{int(now)}",
                            f"{last['temp_c']:.2f}" if last['temp_c'] is not None else "",
                            f"{last['humidity_pct']:.2f}" if last['humidity_pct'] is not None else "",
                            f"{last['battery_mv']}" if last['battery_mv'] is not None else "",
                        ])
                    last_written = now
                    print(f"[LOG] wrote CSV at {datetime.fromtimestamp(now)}", flush=True)
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[WARN] persistent stream error: {e}", flush=True)
            # backoff to avoid hammering when asleep
            await asyncio.sleep(min(backoff, MAX_BACKOFF))
            backoff = min(backoff * 2, MAX_BACKOFF)
        finally:
            try:
                # if we have a client, ensure proper exit
                await client.__aexit__(None, None, None)  # noqa
            except Exception:
                pass

async def periodic_poll():
    """
    Connect, wait for one notify (or a short window), disconnect, sleep, repeat.
    """
    ensure_csv(OUTPUT)
    print(f"[INFO] {VERSION}  MAC={DEVICE_MAC}", flush=True)
    global last, last_written
    backoff = 1

    while True:
        try:
            client = await connect_once()
            print(f"[INFO] connected to {DEVICE_MAC}", flush=True)
            backoff = 1  # reset backoff on success
            vals = await wait_one_notification(client, timeout_s=NOTIFY_WINDOW_SECS)
            await client.__aexit__(None, None, None)

            t, h, mv = vals.get("temp_c"), vals.get("humidity_pct"), vals.get("battery_mv")
            if t is not None or h is not None:
                last.update(vals)
                print(f"[GATT] {DEVICE_MAC} -> "
                      f"{('T=%.2f°C ' % t) if t is not None else ''}"
                      f"{('H=%.2f%% ' % h) if h is not None else ''}"
                      f"{('(batt=%dmV)' % mv) if mv is not None else ''}",
                      flush=True)

        except Exception as e:
            print(f"[WARN] GATT read failed: {e}", flush=True)
            # exponential backoff when asleep / grabbed / out of range
            await asyncio.sleep(min(backoff, MAX_BACKOFF))
            backoff = min(backoff * 2, MAX_BACKOFF)
        else:
            # normal idle between cycles
            await asyncio.sleep(IDLE_BETWEEN_CYCLES)

        # periodic CSV write
        now = time.time()
        if (now - last_written) >= INTERVAL and (last["temp_c"] is not None or last["humidity_pct"] is not None):
            with open(OUTPUT,"a",newline="") as f:
                csv.writer(f).writerow([
                    datetime.utcfromtimestamp(now).isoformat()+"Z",
                    f"{int(now)}",
                    f"{last['temp_c']:.2f}" if last['temp_c'] is not None else "",
                    f"{last['humidity_pct']:.2f}" if last['humidity_pct'] is not None else "",
                    f"{last['battery_mv']}" if last['battery_mv'] is not None else "",
                ])
            last_written = now
            print(f"[LOG] wrote CSV at {datetime.fromtimestamp(now)}", flush=True)

async def main():
    global DEVICE_MAC
    ensure_csv(OUTPUT)

    while True:
        DEVICE_MAC = get_selected_mac()
        if DEVICE_MAC:
            break
        print("[INFO] No device selected yet. Waiting for /api/setup/select...", flush=True)
        await asyncio.sleep(2)

    print(f"[INFO] Using selected MAC from config: {DEVICE_MAC}", flush=True)

    if PERSISTENT_NOTIFY:
        await persistent_stream()
    else:
        await periodic_poll()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
