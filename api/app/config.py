"""Settings, all from environment variables (see .env.example)."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "ws.sqlite3"
MEDIA_DIR = DATA_DIR / "media"
BACKUP_DIR = DATA_DIR / "backups"

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

# Times on the site are shown in this zone (and "today" for rain uses it).
LOCAL_TZ = os.environ.get("LOCAL_TZ", "Asia/Kolkata")

FRAME_RETENTION_DAYS = int(os.environ.get("FRAME_RETENTION_DAYS", "7"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))

# Optional Telegram alert when a station stops sending.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SILENT_ALERT_MINUTES = int(os.environ.get("SILENT_ALERT_MINUTES", "15"))

# Daily camera timelapse: made after this local hour for the previous day, kept N days.
TIMELAPSE_HOUR = int(os.environ.get("TIMELAPSE_HOUR", "1"))
TIMELAPSE_KEEP_DAYS = int(os.environ.get("TIMELAPSE_KEEP_DAYS", "30"))
TIMELAPSE_FPS = int(os.environ.get("TIMELAPSE_FPS", "30"))

# Upload credentials, mounted read-only from the server's ./secrets folder:
#   youtube_token.json  (made with tools/youtube_auth.py)          -> YouTube uploads on
#   vimeo.json          (access_token, client_id, client_secret)   -> Vimeo uploads on
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/secrets"))
YOUTUBE_PRIVACY = os.environ.get("YOUTUBE_PRIVACY", "public")  # public | unlisted | private

# Background jobs (cleanup, backups, alerts). Off in tests.
JOBS_ENABLED = os.environ.get("JOBS_ENABLED", "1") == "1"

# Local development: let the API also serve web/ and media (Caddy does this in production).
SERVE_WEB = os.environ.get("SERVE_WEB", "0") == "1"
WEB_DIR = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
