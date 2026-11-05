"""Station health: the Pi's power, restarts, outages and events, kept on the server
so the history survives the station going down.

- Every reading may carry power_flags (vcgencmd get_throttled) and uptime_s.
- The station can also post events, e.g. a camera photo skipped for low power.
- GET /health turns these into a timeline: low-voltage periods, restarts,
  gaps with no data, and the posted events.
"""
import time
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from .auth import require_station
from .db import connect
from .ingest import _epoch

router = APIRouter(prefix="/api/v1")

UNDERVOLT_NOW = 0x1
THROTTLED_NOW = 0x4
UNDERVOLT_SINCE_BOOT = 0x10000
GAP_S = 5 * 60          # no reading for this long counts as an outage
MAX_EVENTS = 300


class Event(BaseModel):
    ts: Optional[datetime] = None
    kind: str = Field(..., min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    detail: Optional[str] = Field(None, max_length=300)


@router.post("/stations/{slug}/events")
def post_events(slug: str, body: dict, request: Request):
    """Station key. One event `{kind, detail, ts}` or `{"events": [...]}`."""
    station = require_station(request)
    if station["slug"] != slug:
        raise HTTPException(403, "key does not belong to this station")
    raw = body["events"] if isinstance(body.get("events"), list) else [body]
    if len(raw) > 500:
        raise HTTPException(413, "too many events (max 500)")
    now = time.time()
    rows = []
    for i, r in enumerate(raw):
        try:
            e = Event.model_validate(r)
        except ValidationError as exc:
            raise HTTPException(422, [{**x, "event": i} for x in
                                      exc.errors(include_url=False, include_context=False)])
        ts = _epoch(e.ts, now)
        rows.append((station["id"], min(ts, int(now) + 600), e.kind, e.detail))
    with connect() as conn:
        conn.executemany("INSERT INTO station_events (station_id, ts, kind, detail) VALUES (?, ?, ?, ?)", rows)
    return {"ok": True, "stored": len(rows)}


def _flag_words(flags: int) -> str:
    words = []
    if flags & UNDERVOLT_NOW:
        words.append("low voltage now")
    if flags & THROTTLED_NOW:
        words.append("slowed down")
    if not words and flags & UNDERVOLT_SINCE_BOOT:
        words.append("low voltage earlier since restart")
    return ", ".join(words) or "OK"


def timeline(conn, sid: int, start: int, now: float) -> dict:
    rows = conn.execute(
        "SELECT ts, power_flags, uptime_s FROM readings WHERE station_id = ? AND ts >= ? ORDER BY ts",
        (sid, start),
    ).fetchall()
    events: List[dict] = []
    low_from = None
    prev = None
    last_boot = None
    for r in rows:
        ts, flags, up = r["ts"], r["power_flags"], r["uptime_s"]
        if prev is not None and ts - prev["ts"] > GAP_S:
            events.append({"ts": prev["ts"], "end": ts, "kind": "no_data",
                           "text": f"No data for {(ts - prev['ts']) // 60} min"})
        if up is not None:
            boot = int(ts - up)
            if prev is not None and prev["uptime_s"] is not None and up < prev["uptime_s"]:
                events.append({"ts": boot, "kind": "restart", "text": "Station restarted"})
            last_boot = boot
        if flags is not None:
            low = bool(int(flags) & UNDERVOLT_NOW)
            if low and low_from is None:
                low_from = ts
            elif not low and low_from is not None:
                events.append({"ts": low_from, "end": ts, "kind": "low_voltage", "text": "Low voltage"})
                low_from = None
        prev = r
    if low_from is not None:
        events.append({"ts": low_from, "end": None, "kind": "low_voltage", "text": "Low voltage (still going)"})
    if prev is not None and now - prev["ts"] > GAP_S:
        events.append({"ts": prev["ts"], "end": None, "kind": "no_data",
                       "text": f"No data for {int(now - prev['ts']) // 60} min (still going)"})

    for e in conn.execute(
        "SELECT ts, kind, detail FROM station_events WHERE station_id = ? AND ts >= ? ORDER BY ts",
        (sid, start),
    ):
        text = {"camera_skipped_low_power": "Camera photo skipped: low voltage",
                "camera_upload_failed": "Camera photo upload failed"}.get(e["kind"], e["kind"].replace("_", " "))
        events.append({"ts": e["ts"], "kind": e["kind"], "text": text, "detail": e["detail"]})

    events.sort(key=lambda e: e["ts"], reverse=True)
    latest = next((r for r in reversed(rows) if r["power_flags"] is not None), None)
    power = None
    if latest is not None:
        f = int(latest["power_flags"])
        power = {"ts": latest["ts"], "flags": f, "hex": hex(f), "text": _flag_words(f),
                 "ok": not (f & (UNDERVOLT_NOW | THROTTLED_NOW))}
    expected = max(1, int((now - start) // 60))
    return {
        "power": power,
        "last_restart": last_boot,
        "data_pct": round(100 * min(len(rows), expected) / expected, 1),
        "events": events[:MAX_EVENTS],
    }


@router.get("/stations/{slug}/health")
def health(slug: str, days: int = Query(7, ge=1, le=30)):
    now = time.time()
    with connect() as conn:
        row = conn.execute("SELECT id FROM stations WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such station")
        return {"now": int(now), "days": days, **timeline(conn, row["id"], int(now - days * 86400), now)}
