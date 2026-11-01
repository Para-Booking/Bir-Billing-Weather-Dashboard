"""Background upkeep, run in one thread inside the API process:
- delete camera photos older than FRAME_RETENTION_DAYS (hourly)
- back up the database once a day, keeping the newest BACKUP_KEEP copies
- optional Telegram message when a station stops sending, and when it's back
"""
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone

import httpx

from . import config
from .db import connect

log = logging.getLogger("ws.jobs")


def prune_frames(now: float) -> int:
    cutoff = int(now - config.FRAME_RETENTION_DAYS * 86400)
    with connect() as conn:
        rows = conn.execute("SELECT path FROM frames WHERE ts < ?", (cutoff,)).fetchall()
        conn.execute("DELETE FROM frames WHERE ts < ?", (cutoff,))
    for r in rows:
        (config.MEDIA_DIR / r["path"]).unlink(missing_ok=True)
    # remove day folders left empty
    frames_dir = config.MEDIA_DIR / "frames"
    if frames_dir.exists():
        for day_dir in frames_dir.glob("*/*"):
            if day_dir.is_dir() and not any(day_dir.iterdir()):
                day_dir.rmdir()
    if rows:
        log.info("pruned %d old frames", len(rows))
    return len(rows)


def backup(now: float) -> str:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = datetime.fromtimestamp(now, timezone.utc).strftime("ws-%Y%m%d.sqlite3")
    dest = config.BACKUP_DIR / name
    src = sqlite3.connect(config.DB_PATH)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    backups = sorted(config.BACKUP_DIR.glob("ws-*.sqlite3"))
    for old in backups[:-config.BACKUP_KEEP]:
        old.unlink()
    log.info("backup written: %s", dest)
    return str(dest)


def _telegram(text: str) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        log.warning("telegram alert failed: %s", exc)


def check_silent(now: float) -> list:
    """Alert once when a station goes quiet and once when it's back."""
    limit = config.SILENT_ALERT_MINUTES * 60
    messages = []
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.name, (SELECT MAX(ts) FROM readings r WHERE r.station_id = s.id) AS last,
                      COALESCE(a.silent, 0) AS silent
               FROM stations s LEFT JOIN station_alerts a ON a.station_id = s.id"""
        ).fetchall()
        for r in rows:
            if r["last"] is None:
                continue  # never reported; nothing to alert about yet
            quiet = now - r["last"] > limit
            if quiet and not r["silent"]:
                mins = int((now - r["last"]) // 60)
                messages.append(f"⚠️ {r['name']} has sent nothing for {mins} min.")
            elif not quiet and r["silent"]:
                messages.append(f"✅ {r['name']} is reporting again.")
            else:
                continue
            conn.execute(
                "INSERT INTO station_alerts (station_id, silent) VALUES (?, ?) "
                "ON CONFLICT(station_id) DO UPDATE SET silent = excluded.silent",
                (r["id"], int(quiet)),
            )
    for m in messages:
        log.warning(m)
        _telegram(m)
    return messages


def _loop(stop: threading.Event) -> None:
    last_prune = last_backup_day = last_check = 0.0
    while not stop.is_set():
        now = time.time()
        try:
            if now - last_check >= 60:
                last_check = now
                check_silent(now)
            if now - last_prune >= 3600:
                last_prune = now
                prune_frames(now)
            day = now // 86400
            if day != last_backup_day:
                last_backup_day = day
                backup(now)
        except Exception:
            log.exception("background job failed")
        stop.wait(30)


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), name="jobs", daemon=True).start()
    return stop
