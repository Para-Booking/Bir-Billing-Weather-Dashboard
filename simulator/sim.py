#!/usr/bin/env python3
"""Fake stations for local testing. Creates three stations (if needed), backfills
two days of 1-minute readings, then sends a new reading every minute.

    API=http://localhost:8000 ADMIN_KEY=dev python3 simulator/sim.py [--once]

Station keys are kept in simulator/.keys.json. The weather is a rough Bir
valley day: calm mornings, south-westerly valley wind building to a peak
around 15:00, north-easterly drainage wind at night.
"""
import io
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

API = os.environ.get("API", "http://localhost:8000").rstrip("/")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "dev")
KEYS_FILE = Path(__file__).with_name(".keys.json")
IST = ZoneInfo("Asia/Kolkata")

STATIONS = [
    {"slug": "billing-takeoff", "name": "Billing Takeoff", "kind": "takeoff", "lat": 32.054,
     "lon": 76.712, "elevation_m": 2400, "launch_dir_from": 160, "launch_dir_to": 250,
     "sort_order": 1, "_wind": 1.35, "_temp": -6.0},
    {"slug": "bir-landing", "name": "Bir Landing", "kind": "landing", "lat": 32.039,
     "lon": 76.722, "elevation_m": 1430, "sort_order": 2, "_wind": 0.8, "_temp": 0.0},
    {"slug": "bir-colony", "name": "Bir Tibetan Colony", "kind": "other", "lat": 32.034,
     "lon": 76.718, "elevation_m": 1431, "sort_order": 3, "_wind": 0.7, "_temp": 0.3},
]


def weather(st: dict, ts: float) -> dict:
    local = datetime.fromtimestamp(ts, IST)
    h = local.hour + local.minute / 60
    rnd = random.Random(int(ts // 60) * 7919 + sum(map(ord, st["slug"])))
    # valley wind: 0 before 10:00, peak at 15:00, gone by 19:00
    day = max(0.0, math.sin(math.pi * (h - 10) / 9)) if 10 <= h <= 19 else 0.0
    base = (1.2 + 4.8 * day) * st["_wind"]                      # m/s
    gustiness = 0.25 + 0.35 * day
    samples = [max(0.0, base * (1 + rnd.gauss(0, gustiness))) for _ in range(16)]
    direction = 225 if day > 0.15 else 45
    temp = 9 + 11 * max(0.0, math.sin(math.pi * (h - 7) / 14)) + st["_temp"] + rnd.gauss(0, 0.2)
    rh = max(25.0, min(98.0, 85 - 45 * day + rnd.gauss(0, 2)))
    pressure_sl = 1012 + 3 * math.sin(ts / 86400 * 2 * math.pi / 3)
    station_pa = pressure_sl * 100 * (1 - 0.0065 * st["elevation_m"] / (temp + 0.0065 * st["elevation_m"] + 273.15)) ** 5.257
    reading = {
        "ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        "temp_c": round(temp, 2), "rh": round(rh, 1), "pressure_pa": round(station_pa, 1),
        "wind_avg_ms": round(sum(samples) / len(samples), 2),
        "wind_gust_ms": round(max(samples), 2), "wind_lull_ms": round(min(samples), 2),
        "wind_dir_deg": round(direction + rnd.gauss(0, 12 + 20 * gustiness), 1) % 360,
        "wind_dir_var_deg": round(10 + 25 * gustiness, 1),
        "pm25": round(18 + 10 * rnd.random(), 1),
    }
    if int(ts // 60) % 15 == 0:  # rain gauge reports every 15 min
        reading["rain_mm"] = round(rnd.choice([0, 0, 0, 0, 0.2, 0.6]) if 14 <= h <= 17 else 0.0, 1)
    return reading


def frame_jpeg(st: dict, ts: float):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    local = datetime.fromtimestamp(ts, IST)
    h = local.hour + local.minute / 60
    light = max(0.08, math.sin(math.pi * (h - 6) / 13)) if 6 <= h <= 19 else 0.08
    im = Image.new("RGB", (640, 360))
    d = ImageDraw.Draw(im)
    for y in range(360):
        k = y / 360
        d.line([(0, y), (640, y)], fill=(int(90 * light + 40 * k), int(150 * light + 30 * k), int(230 * light)))
    d.polygon([(0, 360), (0, 250), (160, 180), (320, 240), (480, 150), (640, 230), (640, 360)], fill=(40, 60, 45))
    d.text((12, 12), f"{st['name']}  {local:%H:%M}", fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def ensure_stations(client: httpx.Client) -> dict:
    keys = json.loads(KEYS_FILE.read_text()) if KEYS_FILE.exists() else {}
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    for st in STATIONS:
        if st["slug"] in keys:
            continue
        body = {k: v for k, v in st.items() if not k.startswith("_")}
        r = client.post(f"{API}/api/v1/stations", json=body, headers=admin)
        if r.status_code == 409:
            r = client.post(f"{API}/api/v1/stations?rotate_key=true", json=body, headers=admin)
        r.raise_for_status()
        keys[st["slug"]] = r.json()["api_key"]
    KEYS_FILE.write_text(json.dumps(keys, indent=2))
    return keys


def main() -> None:
    once = "--once" in sys.argv
    with httpx.Client(timeout=30) as client:
        keys = ensure_stations(client)
        now = time.time()
        for st in STATIONS:
            auth = {"Authorization": f"Bearer {keys[st['slug']]}"}
            start = int(now // 60) * 60 - 2 * 86400
            batch = [weather(st, t) for t in range(start, int(now), 60)]
            r = client.post(f"{API}/api/v1/ingest", json={"readings": batch}, headers=auth)
            r.raise_for_status()
            sent = 0
            for t in range(int(now // 120) * 120 - 7200, int(now), 120):  # last 2 h of photos
                jpg = frame_jpeg(st, t)
                if jpg:
                    client.post(f"{API}/api/v1/stations/{st['slug']}/frames",
                                params={"ts": datetime.fromtimestamp(t, timezone.utc).isoformat()},
                                files={"frame": ("f.jpg", jpg, "image/jpeg")}, headers=auth)
                    sent += 1
            print(f"{st['slug']}: backfilled {r.json()['stored']} readings, {sent} photos")
        if once:
            return
        while True:
            time.sleep(60 - time.time() % 60 + 1)
            t = time.time()
            for st in STATIONS:
                auth = {"Authorization": f"Bearer {keys[st['slug']]}"}
                try:
                    client.post(f"{API}/api/v1/ingest", json=weather(st, t), headers=auth)
                    if int(t // 60) % 2 == 0 and (jpg := frame_jpeg(st, t)):
                        client.post(f"{API}/api/v1/stations/{st['slug']}/frames",
                                    files={"frame": ("f.jpg", jpg, "image/jpeg")}, headers=auth)
                except httpx.HTTPError as exc:
                    print("send failed:", exc)


if __name__ == "__main__":
    main()
