"""SQLite storage. One file, WAL mode; a new connection per request."""
import sqlite3
from contextlib import contextmanager

from . import config

# Every reading column, in m/s, °C, %, Pa, mm, µg/m³ and degrees.
READING_FIELDS = (
    "temp_c", "rh", "pressure_pa",
    "wind_avg_ms", "wind_gust_ms", "wind_lull_ms",
    "wind_dir_deg", "wind_dir_var_deg",
    "rain_mm", "pm1", "pm25", "pm10",
)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS stations (
    id               INTEGER PRIMARY KEY,
    slug             TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'other',  -- takeoff | landing | other
    lat              REAL,
    lon              REAL,
    elevation_m      REAL,
    launch_dir_from  REAL,   -- good wind directions for launch, clockwise from..to
    launch_dir_to    REAL,
    band_light_max_kmh    REAL NOT NULL DEFAULT 10,  -- wind bands use the gust
    band_moderate_max_kmh REAL NOT NULL DEFAULT 18,
    band_strong_max_kmh   REAL NOT NULL DEFAULT 25,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    api_key_hash     TEXT NOT NULL,
    created_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS readings (
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    ts         INTEGER NOT NULL,  -- unix seconds, UTC
    {", ".join(f + " REAL" for f in READING_FIELDS)},
    PRIMARY KEY (station_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS frames (
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    ts         INTEGER NOT NULL,
    path       TEXT NOT NULL,     -- relative to MEDIA_DIR
    PRIMARY KEY (station_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS timelapses (
    station_id    INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    day           TEXT NOT NULL,      -- local date, YYYY-MM-DD
    path          TEXT NOT NULL,      -- relative to MEDIA_DIR
    frames        INTEGER NOT NULL,
    created       INTEGER NOT NULL,
    youtube_url   TEXT,               -- NULL = not uploaded yet, '-' = not wanted
    vimeo_url     TEXT,
    upload_tries  INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    PRIMARY KEY (station_id, day)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS station_alerts (
    station_id INTEGER PRIMARY KEY REFERENCES stations(id) ON DELETE CASCADE,
    silent     INTEGER NOT NULL DEFAULT 0
);
"""


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def connect():
    """Connection that commits on success and always closes."""
    conn = _open()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    conn = _open()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()
