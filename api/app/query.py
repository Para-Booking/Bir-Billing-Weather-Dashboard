"""Read side: public, no key needed. All speeds are m/s; the browser converts."""
import time
from datetime import datetime
from statistics import mean
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from . import config, wx
from .db import READING_FIELDS, connect

router = APIRouter(prefix="/api/v1")

LOOKBACK_S = 24 * 3600        # how far back to look for a sensor's last value
DERIVE_MAX_AGE_S = 15 * 60    # inputs to derived values must be this fresh
RAW_SERIES_MAX_MIN = 48 * 60  # beyond this, series come in 10-minute buckets
BUCKET_S = 600
TREND_KMH = 3.0               # wind change that counts as rising/falling

STATION_COLS = (
    "slug", "name", "kind", "lat", "lon", "elevation_m", "launch_dir_from",
    "launch_dir_to", "band_light_max_kmh", "band_moderate_max_kmh",
    "band_strong_max_kmh", "sort_order",
)


def _r(value, digits=2):
    return None if value is None else round(value, digits)


def _stations(conn, slug: Optional[str] = None) -> List[dict]:
    sql = f"SELECT id, {', '.join(STATION_COLS)} FROM stations"
    if slug:
        rows = conn.execute(sql + " WHERE slug = ?", (slug,)).fetchall()
        if not rows:
            raise HTTPException(404, "no such station")
    else:
        rows = conn.execute(sql + " ORDER BY sort_order, name").fetchall()
    return [dict(r) for r in rows]


def _meta(st: dict) -> dict:
    launch = None
    if st["launch_dir_from"] is not None and st["launch_dir_to"] is not None:
        launch = {"from": st["launch_dir_from"], "to": st["launch_dir_to"]}
    return {
        "slug": st["slug"], "name": st["name"], "kind": st["kind"],
        "lat": st["lat"], "lon": st["lon"], "elevation_m": st["elevation_m"],
        "launch": launch,
        "bands_kmh": {
            "light": st["band_light_max_kmh"],
            "moderate": st["band_moderate_max_kmh"],
            "strong": st["band_strong_max_kmh"],
        },
    }


def latest_values(conn, sid: int, now: float, fields=READING_FIELDS) -> Dict[str, dict]:
    """Most recent non-empty value of each field (within 24 h) and its time."""
    out = {}
    for f in fields:
        row = conn.execute(
            f"SELECT ts, {f} AS v FROM readings WHERE station_id = ? AND ts > ? "
            f"AND {f} IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (sid, int(now - LOOKBACK_S)),
        ).fetchone()
        if row:
            out[f] = {"v": row["v"], "ts": row["ts"]}
    return out


def _value_near(conn, sid: int, field: str, target: int, tolerance: int) -> Optional[float]:
    row = conn.execute(
        f"SELECT {field} AS v FROM readings WHERE station_id = ? AND ts BETWEEN ? AND ? "
        f"AND {field} IS NOT NULL ORDER BY abs(ts - ?) LIMIT 1",
        (sid, target - tolerance, target + tolerance, target),
    ).fetchone()
    return None if row is None else row["v"]


def wind_trend(conn, sid: int, ref_ts: int) -> Optional[dict]:
    """Average wind of the last 5 minutes vs 15-20 minutes earlier."""
    rows = conn.execute(
        "SELECT ts, wind_avg_ms FROM readings WHERE station_id = ? AND ts > ? AND ts <= ? "
        "AND wind_avg_ms IS NOT NULL",
        (sid, ref_ts - 20 * 60, ref_ts),
    ).fetchall()
    recent = [r["wind_avg_ms"] for r in rows if r["ts"] > ref_ts - 5 * 60]
    before = [r["wind_avg_ms"] for r in rows if r["ts"] <= ref_ts - 15 * 60]
    if not recent or not before:
        return None
    change = (mean(recent) - mean(before)) * 3.6
    word = "rising" if change >= TREND_KMH else "falling" if change <= -TREND_KMH else "steady"
    return {"trend": word, "change_kmh": round(change, 1)}


def _derived(conn, st: dict, vals: Dict[str, dict], now: float) -> dict:
    sid = st["id"]
    d: dict = {}

    def fresh(f):
        v = vals.get(f)
        return v if v and now - v["ts"] <= DERIVE_MAX_AGE_S else None

    t, rh, p = fresh("temp_c"), fresh("rh"), fresh("pressure_pa")
    if t and rh:
        dew = wx.dew_point_c(t["v"], rh["v"])
        if dew is not None:
            agl = wx.cloud_base_agl_m(t["v"], dew)
            d["dew_point_c"] = round(dew, 1)
            d["cloud_base_agl_m"] = int(round(agl, -1))
            if st["elevation_m"] is not None:
                d["cloud_base_amsl_m"] = int(round(agl + st["elevation_m"], -1))
    if t:
        before = _value_near(conn, sid, "temp_c", t["ts"] - 3600, 600)
        if before is not None:
            d["temp_change_1h_c"] = round(t["v"] - before, 1)
    if p:
        if t and st["elevation_m"] is not None:
            d["pressure_sl_hpa"] = round(wx.sea_level_hpa(p["v"], t["v"], st["elevation_m"]), 1)
        d["pressure_station_hpa"] = round(p["v"] / 100.0, 1)
        before = _value_near(conn, sid, "pressure_pa", p["ts"] - 3 * 3600, 1200)
        if before is not None:
            d["pressure_change_3h_hpa"] = round((p["v"] - before) / 100.0, 1)

    if "rain_mm" in vals:
        midnight = datetime.fromtimestamp(now, ZoneInfo(config.LOCAL_TZ)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        def rain_since(start):
            row = conn.execute(
                "SELECT COALESCE(SUM(rain_mm), 0) AS s FROM readings "
                "WHERE station_id = ? AND ts > ?", (sid, int(start)),
            ).fetchone()
            return round(row["s"], 1)

        d["rain_1h_mm"] = rain_since(now - 3600)
        d["rain_today_mm"] = rain_since(midnight.timestamp())
        d["raining"] = rain_since(now - 20 * 60) > 0

    w = vals.get("wind_avg_ms")
    if w:
        trend = wind_trend(conn, sid, w["ts"])
        if trend:
            d["wind_trend"] = trend
    return d


def _wind_strip(conn, sid: int, minutes: int, now: float) -> dict:
    rows = conn.execute(
        "SELECT ts, wind_avg_ms, wind_gust_ms FROM readings WHERE station_id = ? AND ts > ? "
        "AND wind_avg_ms IS NOT NULL ORDER BY ts",
        (sid, int(now - minutes * 60)),
    ).fetchall()
    return {
        "ts": [r["ts"] for r in rows],
        "avg": [_r(r["wind_avg_ms"], 1) for r in rows],
        "gust": [_r(r["wind_gust_ms"], 1) for r in rows],
    }


def _latest_frame(conn, sid: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT ts, path FROM frames WHERE station_id = ? ORDER BY ts DESC LIMIT 1", (sid,)
    ).fetchone()
    return None if row is None else {"ts": row["ts"], "url": "/media/" + row["path"]}


def _sun(st: dict, now: float) -> Optional[dict]:
    if st["lat"] is None or st["lon"] is None:
        return None
    today = datetime.fromtimestamp(now, ZoneInfo(config.LOCAL_TZ)).date()
    rise, sset = wx.sun_times(st["lat"], st["lon"], today, config.LOCAL_TZ)
    return {"sunrise": rise, "sunset": sset}


def summarize(conn, st: dict, now: float, strip_minutes: int = 60) -> dict:
    vals = latest_values(conn, st["id"], now)
    last = conn.execute(
        "SELECT MAX(ts) AS ts FROM readings WHERE station_id = ?", (st["id"],)
    ).fetchone()["ts"]
    return {
        **_meta(st),
        "last_seen": last,
        "values": {k: {"v": _r(v["v"]), "ts": v["ts"]} for k, v in vals.items()},
        "derived": _derived(conn, st, vals, now),
        "wind_strip": _wind_strip(conn, st["id"], strip_minutes, now),
        "frame": _latest_frame(conn, st["id"]),
        "sun": _sun(st, now),
    }


@router.get("/config")
def site_config():
    return {"tz": config.LOCAL_TZ}


@router.get("/stations")
def list_stations():
    now = time.time()
    with connect() as conn:
        return {"now": int(now), "tz": config.LOCAL_TZ,
                "stations": [summarize(conn, st, now) for st in _stations(conn)]}


@router.get("/stations/{slug}")
def station(slug: str):
    now = time.time()
    with connect() as conn:
        st = _stations(conn, slug)[0]
        return {"now": int(now), "tz": config.LOCAL_TZ, "station": summarize(conn, st, now)}


def _air(conn, st: dict, now: float) -> Optional[dict]:
    """Temperature, humidity and sea-level pressure with its 3-hour change."""
    vals = latest_values(conn, st["id"], now, ("temp_c", "rh", "pressure_pa"))
    if not vals:
        return None
    d = _derived(conn, st, vals, now)
    return {
        "ts": max(v["ts"] for v in vals.values()),
        "temp_c": _r(vals["temp_c"]["v"], 1) if "temp_c" in vals else None,
        "rh": _r(vals["rh"]["v"], 0) if "rh" in vals else None,
        "pressure_sl_hpa": d.get("pressure_sl_hpa"),
        "pressure_change_3h_hpa": d.get("pressure_change_3h_hpa"),
    }


@router.get("/fly")
def fly():
    """Compact payload for flight mode: wind (last 30 minutes) plus one line
    of temperature, humidity and pressure."""
    now = time.time()
    wind_fields = ("wind_avg_ms", "wind_gust_ms", "wind_lull_ms", "wind_dir_deg", "wind_dir_var_deg")
    out = []
    with connect() as conn:
        for st in _stations(conn):
            vals = latest_values(conn, st["id"], now, wind_fields)
            meta = _meta(st)
            w = vals.get("wind_avg_ms")
            out.append({
                "slug": meta["slug"], "name": meta["name"], "kind": meta["kind"],
                "elevation_m": meta["elevation_m"], "launch": meta["launch"],
                "bands_kmh": meta["bands_kmh"],
                "values": {k: {"v": _r(v["v"], 1), "ts": v["ts"]} for k, v in vals.items()},
                "trend": wind_trend(conn, st["id"], w["ts"]) if w else None,
                "strip": _wind_strip(conn, st["id"], 30, now),
                "air": _air(conn, st, now),
            })
    return {"now": int(now), "stations": out}


def _bucket(rows: List[dict], metrics: List[str]) -> dict:
    groups: Dict[int, List[dict]] = {}
    for r in rows:
        groups.setdefault(r["ts"] - r["ts"] % BUCKET_S, []).append(r)
    ts_out, series = [], {m: [] for m in metrics}
    for start in sorted(groups):
        g = groups[start]
        ts_out.append(start + BUCKET_S // 2)
        for m in metrics:
            vals = [r[m] for r in g if r[m] is not None]
            if not vals:
                series[m].append(None)
            elif m == "wind_gust_ms":
                series[m].append(_r(max(vals)))
            elif m == "wind_lull_ms":
                series[m].append(_r(min(vals)))
            elif m == "rain_mm":
                series[m].append(_r(sum(vals)))
            elif m == "wind_dir_deg":
                series[m].append(_r(wx.circular_mean_deg(vals), 1))
            else:
                series[m].append(_r(mean(vals)))
    return {"ts": ts_out, "series": series}


@router.get("/stations/{slug}/series")
def series(
    slug: str,
    minutes: int = Query(60, ge=1, le=7 * 24 * 60),
    metrics: str = Query("wind_avg_ms,wind_gust_ms,wind_lull_ms,wind_dir_deg"),
):
    cols = [m.strip() for m in metrics.split(",") if m.strip()]
    bad = [c for c in cols if c not in READING_FIELDS]
    if bad or not cols:
        raise HTTPException(422, f"unknown metrics: {', '.join(bad) or '(none)'}")
    now = time.time()
    with connect() as conn:
        sid = _stations(conn, slug)[0]["id"]
        rows = [dict(r) for r in conn.execute(
            f"SELECT ts, {', '.join(cols)} FROM readings WHERE station_id = ? AND ts > ? ORDER BY ts",
            (sid, int(now - minutes * 60)),
        )]
    if minutes > RAW_SERIES_MAX_MIN:
        data = _bucket(rows, cols)
        step = BUCKET_S
    else:
        data = {"ts": [r["ts"] for r in rows], "series": {c: [_r(r[c]) for r in rows] for c in cols}}
        step = 60
    return {"minutes": minutes, "step_s": step, **data}


@router.get("/stations/{slug}/frames")
def frames(slug: str, minutes: int = Query(120, ge=1, le=24 * 60)):
    now = time.time()
    with connect() as conn:
        sid = _stations(conn, slug)[0]["id"]
        rows = conn.execute(
            "SELECT ts, path FROM frames WHERE station_id = ? AND ts > ? ORDER BY ts LIMIT 500",
            (sid, int(now - minutes * 60)),
        ).fetchall()
    return {"frames": [{"ts": r["ts"], "url": "/media/" + r["path"]} for r in rows]}
