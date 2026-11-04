"""Write side: readings, camera frames, and station management (admin).

The ingest format matches forsyth's (github.com/m-anish/forsyth), so a station
can send the same payload to both. Extra fields here: wind_lull_ms and
wind_dir_var_deg. Unknown fields are ignored.
"""
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError

from . import config, timelapse
from .auth import hash_key, new_station_key, require_admin, require_station
from .db import READING_FIELDS, connect

router = APIRouter(prefix="/api/v1")

MAX_BATCH = 5000
MAX_FRAME_BYTES = 5 * 1024 * 1024
FUTURE_TOLERANCE_S = 600


class Reading(BaseModel):
    ts: Optional[datetime] = None
    temp_c: Optional[float] = Field(None, ge=-60, le=60)
    rh: Optional[float] = Field(None, ge=0, le=100)
    pressure_pa: Optional[float] = Field(None, gt=30000, lt=120000)  # station pressure
    wind_avg_ms: Optional[float] = Field(None, ge=0, le=100)
    wind_gust_ms: Optional[float] = Field(None, ge=0, le=100)
    wind_lull_ms: Optional[float] = Field(None, ge=0, le=100)
    wind_dir_deg: Optional[float] = Field(None, ge=0, lt=360)
    wind_dir_var_deg: Optional[float] = Field(None, ge=0, le=180)
    rain_mm: Optional[float] = Field(None, ge=0, le=500)  # since previous report
    pm1: Optional[float] = Field(None, ge=0)
    pm25: Optional[float] = Field(None, ge=0)
    pm10: Optional[float] = Field(None, ge=0)


def _epoch(ts: Optional[datetime], now: float) -> int:
    if ts is None:
        return int(now)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    value = int(ts.timestamp())
    if value > now + FUTURE_TOLERANCE_S:
        raise HTTPException(422, "reading timestamp is in the future (check the station clock)")
    return value


def store_readings(station_id: int, readings: List[Reading]) -> int:
    now = time.time()
    rows = [
        (station_id, _epoch(r.ts, now), *(getattr(r, f) for f in READING_FIELDS))
        for r in readings
    ]
    cols = ", ".join(READING_FIELDS)
    marks = ", ".join("?" for _ in READING_FIELDS)
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            f"INSERT OR IGNORE INTO readings (station_id, ts, {cols}) VALUES (?, ?, {marks})",
            rows,
        )
        return conn.total_changes - before


@router.post("/ingest")
def ingest(body: dict, request: Request):
    """One reading `{...}` or a batch `{"readings": [{...}, ...]}`."""
    station = require_station(request)
    raw = body["readings"] if isinstance(body.get("readings"), list) else [body]
    if len(raw) > MAX_BATCH:
        raise HTTPException(413, f"batch too large (max {MAX_BATCH})")
    readings = []
    for i, r in enumerate(raw):
        try:
            readings.append(Reading.model_validate(r))
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False)
            raise HTTPException(422, [{**e, "reading": i} for e in errors])
    stored = store_readings(station["id"], readings)
    return {"ok": True, "stored": stored}


@router.post("/stations/{slug}/frames")
async def upload_frame(
    slug: str,
    request: Request,
    ts: Optional[datetime] = None,
    frame: UploadFile = File(...),
):
    station = require_station(request)
    if station["slug"] != slug:
        raise HTTPException(403, "key does not belong to this station")
    data = await frame.read()
    if len(data) > MAX_FRAME_BYTES:
        raise HTTPException(413, "frame too large (max 5 MB)")
    if not data.startswith(b"\xff\xd8"):
        raise HTTPException(415, "JPEG only")

    epoch = _epoch(ts, time.time())
    when = datetime.fromtimestamp(epoch, timezone.utc)
    rel = Path("frames") / slug / when.strftime("%Y-%m-%d") / (when.strftime("%H%M%S") + ".jpg")
    dest = config.MEDIA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO frames (station_id, ts, path) VALUES (?, ?, ?)",
            (station["id"], epoch, str(rel)),
        )
    return {"ok": True, "path": str(rel)}


# ---------- station management (admin key) ----------

KINDS = ("takeoff", "landing", "other")


class StationFields(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    kind: Optional[str] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    elevation_m: Optional[float] = Field(None, ge=-500, le=9000)
    launch_dir_from: Optional[float] = Field(None, ge=0, lt=360)
    launch_dir_to: Optional[float] = Field(None, ge=0, lt=360)
    band_light_max_kmh: Optional[float] = Field(None, gt=0, le=200)
    band_moderate_max_kmh: Optional[float] = Field(None, gt=0, le=200)
    band_strong_max_kmh: Optional[float] = Field(None, gt=0, le=200)
    sort_order: Optional[int] = None


class StationCreate(StationFields):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)


REQUIRED = ("name", "kind", "band_light_max_kmh", "band_moderate_max_kmh",
            "band_strong_max_kmh", "sort_order")


def _check(fields: dict, current: Optional[dict] = None) -> None:
    for name in REQUIRED:
        if name in fields and fields[name] is None:
            raise HTTPException(422, f"{name} cannot be empty")
    if "kind" in fields and fields["kind"] not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    merged = {**(current or {}), **fields}
    bands = [merged.get(k) for k in ("band_light_max_kmh", "band_moderate_max_kmh", "band_strong_max_kmh")]
    if all(b is not None for b in bands) and not bands[0] < bands[1] < bands[2]:
        raise HTTPException(422, "wind bands must increase: light < moderate < strong")


@router.post("/stations", status_code=201)
def create_station(body: StationCreate, request: Request, rotate_key: bool = Query(False)):
    """Create a station and return its key (shown only once).
    With ?rotate_key=true on an existing slug, issue a new key instead."""
    require_admin(request)
    key = new_station_key()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM stations WHERE slug = ?", (body.slug,)).fetchone()
        if existing:
            if not rotate_key:
                raise HTTPException(409, "station exists; use PATCH to edit or ?rotate_key=true for a new key")
            conn.execute("UPDATE stations SET api_key_hash = ? WHERE id = ?", (hash_key(key), existing["id"]))
            return {"slug": body.slug, "api_key": key, "rotated": True}
        fields = body.model_dump(exclude_none=True)
        _check(fields)
        fields.update(api_key_hash=hash_key(key), created_at=int(time.time()))
        cols = ", ".join(fields)
        conn.execute(
            f"INSERT INTO stations ({cols}) VALUES ({', '.join('?' for _ in fields)})",
            tuple(fields.values()),
        )
    return {"slug": body.slug, "api_key": key}


@router.patch("/stations/{slug}")
def update_station(slug: str, body: StationFields, request: Request):
    require_admin(request)
    fields = body.model_dump(exclude_unset=True)
    with connect() as conn:
        row = conn.execute("SELECT * FROM stations WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such station")
        _check(fields, dict(row))
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE stations SET {sets} WHERE slug = ?", (*fields.values(), slug))
    return {"ok": True, "updated": sorted(fields)}


@router.delete("/stations/{slug}")
def delete_station(slug: str, request: Request):
    require_admin(request)
    with connect() as conn:
        n = conn.execute("DELETE FROM stations WHERE slug = ?", (slug,)).rowcount
    if not n:
        raise HTTPException(404, "no such station")
    return {"ok": True}


@router.post("/stations/{slug}/timelapses")
def make_timelapse(slug: str, request: Request, day: date, upload: bool = True):
    """Admin: (re)build one local day's timelapse now, e.g. to test or to redo a day.
    With upload=false it is only shown on the site, never sent to YouTube/Vimeo."""
    require_admin(request)
    with connect() as conn:
        st = conn.execute("SELECT id, slug, name FROM stations WHERE slug = ?", (slug,)).fetchone()
    if st is None:
        raise HTTPException(404, "no such station")
    with timelapse._lock:
        row = timelapse.make(dict(st), day, upload=upload)
    if row is None:
        raise HTTPException(409, f"fewer than {timelapse.MIN_FRAMES} photos that day")
    return {"day": row["day"], "url": "/media/" + row["path"], "frames": row["frames"],
            "uploads": "queued (next hourly run)" if upload else "off"}
