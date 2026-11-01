# Bir Billing Wind

Live wind and weather from stations around Bir Billing, for paragliding pilots.
Observations only, no forecasts. Hosted at **https://ws.birbilling.live**.

- **Overview** `/`: every station at a glance, takeoff first.
- **Station page** `/s/<slug>`: wind now, wind history (30 min to 7 days), live camera,
  thermals and estimated cloud base, pressure trend, rain, sun, haze, station health.
- **Flight mode** `/fly`: one station per screen in huge black-on-white type for use while
  flying. It keeps the screen on, refreshes every 20 s, can read the wind aloud, and shows the last
  values with a clear warning when offline.

Stations send data to the same API format as [forsyth](https://github.com/m-anish/forsyth),
so one station can feed both.

## How it fits together

```
Raspberry Pi station ──HTTPS──▶ Caddy ──▶ API (FastAPI + SQLite)
                                  │
pilots' phones ◀──────────────────┘ static site (web/) + photos (/media)
```

| Folder | What |
|---|---|
| `api/` | FastAPI app: ingest, public read endpoints, background jobs (photo cleanup, nightly backup, offline alerts) |
| `web/` | The site: plain HTML/CSS/JS, uPlot for charts, no build step |
| `simulator/` | Three fake stations for local testing |
| `Caddyfile`, `docker-compose.yml` | Production: HTTPS + the API |

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt pytest pillow
cd api && DATA_DIR=../data ADMIN_KEY=dev SERVE_WEB=1 ../.venv/bin/uvicorn app.main:app --port 8000
# in another terminal: fake stations, two days of history, then a reading every minute
API=http://localhost:8000 ADMIN_KEY=dev .venv/bin/python simulator/sim.py
```

Open http://localhost:8000. Tests: `cd api && ../.venv/bin/python -m pytest -q`.

## Deploy (one-time setup)

1. **Create a droplet** on DigitalOcean: Basic, Regular SSD, **1 vCPU / 2 GB / 50 GB**
   (about US$12/month), region **BLR1**, Ubuntu 24.04, your SSH key, weekly backups on.
2. **DNS:** where birbilling.live's DNS is managed, add an `A` record `ws` → the droplet's IP
   (if it's on Cloudflare, use "DNS only", grey cloud).
3. **On the droplet**, as root:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ufw allow OpenSSH && ufw allow 80,443/tcp && ufw allow 443/udp && ufw --force enable
   git clone <this repo's URL> /opt/birbilling-ws && cd /opt/birbilling-ws
   cp .env.example .env && nano .env      # set ADMIN_KEY (long random string)
   docker compose up -d --build
   ```
   Caddy fetches the HTTPS certificate on first request. Check `https://ws.birbilling.live/api/health`.
4. **Register each station** (the key is shown only once; save it):
   ```bash
   curl -X POST https://ws.birbilling.live/api/v1/stations \
     -H "Authorization: Bearer $ADMIN_KEY" -H 'Content-Type: application/json' \
     -d '{"slug":"bir-colony","name":"Bir Tibetan Colony","kind":"landing",
          "lat":32.034,"lon":76.718,"elevation_m":1431}'
   ```
   For a takeoff, add its good launch directions, e.g. `"launch_dir_from":160,"launch_dir_to":250`.
   Wind band limits (km/h, judged on the gust) default to 10 / 18 / 25. Change any field later with
   `PATCH /api/v1/stations/<slug>`. A lost key can be replaced with
   `POST /api/v1/stations?rotate_key=true` (same body).
5. **Point the Pi at it.** See the station repo's README (`UPLINKS` in `key.json`).

Updates afterwards: `ssh` in and run `./deploy.sh`.

## API

| Method | Path | Who |
|---|---|---|
| POST | `/api/v1/ingest` | station key. One reading or `{"readings":[...]}` |
| POST | `/api/v1/stations/<slug>/frames?ts=` | station key. JPEG camera frame (field `frame`) |
| GET | `/api/v1/stations` · `/api/v1/stations/<slug>` | public. Latest values, derived values, last 60 min |
| GET | `/api/v1/stations/<slug>/series?minutes=&metrics=` | public. 1-minute data up to 48 h, 10-minute summaries beyond |
| GET | `/api/v1/stations/<slug>/frames?minutes=` | public. Photo list |
| GET | `/api/v1/fly` | public. Compact wind data for flight mode |
| POST/PATCH/DELETE | `/api/v1/stations…` | admin key |

Reading fields: `ts` (ISO time), `temp_c`, `rh`, `pressure_pa` (station pressure),
`wind_avg_ms`, `wind_gust_ms`, `wind_lull_ms`, `wind_dir_deg`, `wind_dir_var_deg`,
`rain_mm` (since the previous report), `pm1`, `pm25`, `pm10`. Speeds in m/s. Every field is
optional, and readings already stored for the same station and time are ignored.
Interactive docs: `/api/docs`.

## Data kept

- **Readings:** kept forever (about 0.5 M rows per station per year).
- **Camera photos:** kept for 7 days (`FRAME_RETENTION_DAYS`).
- **Database backup:** nightly into the `appdata` volume, newest 14 kept. Copy one off the server now and then:
  `docker compose cp api:/data/backups ./backups`.
