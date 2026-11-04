"""Daily camera timelapse for each station, made on the server from the stored
camera photos (the Pi no longer has to keep photos or run ffmpeg).

Each night after TIMELAPSE_HOUR (local time) the previous day's photos become
media/timelapse/<slug>/<day>.mp4, shown on the station page and kept for
TIMELAPSE_KEEP_DAYS. When credentials are present in SECRETS_DIR the video is
also uploaded to YouTube and/or Vimeo; failed uploads are retried hourly, up
to MAX_UPLOAD_TRIES times.
"""
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from . import config
from .db import connect

log = logging.getLogger("ws.timelapse")

MIN_FRAMES = 10
MAX_UPLOAD_TRIES = 6
NOT_WANTED = "-"
_lock = threading.Lock()  # one ffmpeg/upload at a time (job thread and admin endpoint)


def _tz() -> ZoneInfo:
    return ZoneInfo(config.LOCAL_TZ)


def _day_bounds(day: date):
    start = datetime(day.year, day.month, day.day, tzinfo=_tz())
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def youtube_enabled() -> bool:
    return (config.SECRETS_DIR / "youtube_token.json").is_file()


def vimeo_enabled() -> bool:
    return (config.SECRETS_DIR / "vimeo.json").is_file()


# ---------- making the video ----------

def make(station: dict, day: date, upload: bool = True) -> Optional[dict]:
    """Build (or rebuild) one station's video for one local day.
    Returns the row, or None when there are too few photos."""
    start, end = _day_bounds(day)
    with connect() as conn:
        paths = [r["path"] for r in conn.execute(
            "SELECT path FROM frames WHERE station_id = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (station["id"], start, end))]
    paths = [config.MEDIA_DIR / p for p in paths if (config.MEDIA_DIR / p).is_file()]
    if len(paths) < MIN_FRAMES:
        log.info("%s %s: only %d photos, no timelapse", station["slug"], day, len(paths))
        return None

    rel = Path("timelapse") / station["slug"] / f"{day.isoformat()}.mp4"
    dest = config.MEDIA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="tl-", dir=config.DATA_DIR))
    try:
        # Numbered links let ffmpeg read any number of photos in order.
        for i, p in enumerate(paths):
            (work / f"{i:06d}.jpg").symlink_to(p)
        tmp_out = work / "out.mp4"
        cmd = [
            "nice", "-n", "10", "ffmpeg", "-loglevel", "error", "-y",
            "-framerate", str(config.TIMELAPSE_FPS), "-i", str(work / "%06d.jpg"),
            "-vf", "scale=1280:-2,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-movflags", "+faststart", str(tmp_out),
        ]
        t0 = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[-500:]}")
        shutil.move(str(tmp_out), dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("%s %s: timelapse from %d photos in %.0f s (%.1f MB)", station["slug"], day,
             len(paths), time.time() - t0, dest.stat().st_size / 1e6)

    yt = None if (upload and youtube_enabled()) else NOT_WANTED
    vm = None if (upload and vimeo_enabled()) else NOT_WANTED
    with connect() as conn:
        conn.execute(
            """INSERT INTO timelapses (station_id, day, path, frames, created, youtube_url, vimeo_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(station_id, day) DO UPDATE SET
                 path = excluded.path, frames = excluded.frames, created = excluded.created,
                 youtube_url = excluded.youtube_url, vimeo_url = excluded.vimeo_url,
                 upload_tries = 0, last_error = NULL""",
            (station["id"], day.isoformat(), str(rel), len(paths), int(time.time()), yt, vm),
        )
        return dict(conn.execute("SELECT * FROM timelapses WHERE station_id = ? AND day = ?",
                                 (station["id"], day.isoformat())).fetchone())


# ---------- uploads ----------

def _title_and_description(station: dict, day: str):
    title = f"{station['name']} weather cam timelapse {day}"
    lines = [f"24-hour timelapse from the {station['name']} weather station, Bir Billing.",
             f"Date: {day}"]
    if station.get("elevation_m") is not None:
        lines.append(f"Elevation: {station['elevation_m']:.0f} m")
    if station.get("lat") is not None and station.get("lon") is not None:
        lines.append(f"Coordinates: {station['lat']}, {station['lon']}")
    lines.append(f"Live data: https://ws.birbilling.live/s/{station['slug']}")
    return title, "\n".join(lines)


def upload_youtube(path: Path, title: str, description: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials.from_authorized_user_file(str(config.SECRETS_DIR / "youtube_token.json"))
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {"title": title[:100], "description": description,
                    "tags": ["weather", "timelapse", "paragliding", "Bir Billing"],
                    "categoryId": "28", "defaultLanguage": "en"},
        "status": {"privacyStatus": config.YOUTUBE_PRIVACY, "license": "creativeCommon",
                   "embeddable": True, "publicStatsViewable": True,
                   "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), mimetype="video/mp4",
                                                        chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return f"https://youtu.be/{resp['id']}"


def upload_vimeo(path: Path, title: str, description: str) -> str:
    import vimeo

    cfg = json.loads((config.SECRETS_DIR / "vimeo.json").read_text())
    client = vimeo.VimeoClient(token=cfg["access_token"], key=cfg.get("client_id"),
                               secret=cfg.get("client_secret"))
    uri = client.upload(str(path), data={"name": title, "description": description})
    return client.get(uri + "?fields=link").json()["link"]


def upload_pending() -> None:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT t.*, s.slug, s.name, s.lat, s.lon, s.elevation_m
               FROM timelapses t JOIN stations s ON s.id = t.station_id
               WHERE (t.youtube_url IS NULL OR t.vimeo_url IS NULL) AND t.upload_tries < ?
               ORDER BY t.day""", (MAX_UPLOAD_TRIES,))]
    for row in rows:
        path = config.MEDIA_DIR / row["path"]
        title, desc = _title_and_description(row, row["day"])
        errors = []
        for col, enabled, fn in (("youtube_url", youtube_enabled, upload_youtube),
                                 ("vimeo_url", vimeo_enabled, upload_vimeo)):
            if row[col] is not None:
                continue
            if not enabled():
                row[col] = NOT_WANTED
                continue
            try:
                row[col] = fn(path, title, desc)
                log.info("%s %s uploaded: %s", row["slug"], row["day"], row[col])
            except Exception as exc:  # network, quota, expired login...
                errors.append(f"{col.split('_')[0]}: {exc}")
                log.warning("%s %s %s upload failed: %s", row["slug"], row["day"], col, exc)
        with connect() as conn:
            conn.execute(
                """UPDATE timelapses SET youtube_url = ?, vimeo_url = ?,
                     upload_tries = upload_tries + ?, last_error = ?
                   WHERE station_id = ? AND day = ?""",
                (row["youtube_url"], row["vimeo_url"], 1 if errors else 0,
                 "; ".join(errors)[:1000] or None, row["station_id"], row["day"]),
            )


# ---------- housekeeping and the nightly loop ----------

def prune(today: date) -> None:
    cutoff = (today - timedelta(days=config.TIMELAPSE_KEEP_DAYS)).isoformat()
    with connect() as conn:
        rows = conn.execute("SELECT path FROM timelapses WHERE day < ?", (cutoff,)).fetchall()
        conn.execute("DELETE FROM timelapses WHERE day < ?", (cutoff,))
    for r in rows:
        (config.MEDIA_DIR / r["path"]).unlink(missing_ok=True)


def run_once(now: float, tried: set) -> None:
    local = datetime.fromtimestamp(now, _tz())
    if local.hour < config.TIMELAPSE_HOUR:
        return
    yesterday = local.date() - timedelta(days=1)
    with connect() as conn:
        stations = [dict(r) for r in conn.execute(
            """SELECT id, slug, name FROM stations s WHERE NOT EXISTS
                 (SELECT 1 FROM timelapses t WHERE t.station_id = s.id AND t.day = ?)""",
            (yesterday.isoformat(),))]
    for st in stations:
        if (st["id"], yesterday) in tried:
            continue
        tried.add((st["id"], yesterday))
        with _lock:
            try:
                make(st, yesterday)
            except Exception:
                log.exception("%s %s: timelapse failed", st["slug"], yesterday)
    prune(local.date())


def _loop(stop: threading.Event) -> None:
    tried: set = set()
    last_upload = 0.0
    while not stop.is_set():
        now = time.time()
        try:
            run_once(now, tried)
            if now - last_upload >= 3600:
                last_upload = now
                with _lock:
                    upload_pending()
        except Exception:
            log.exception("timelapse job failed")
        stop.wait(300)


def start(stop: threading.Event) -> None:
    threading.Thread(target=_loop, args=(stop,), name="timelapse", daemon=True).start()
