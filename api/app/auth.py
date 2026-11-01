"""Two kinds of key: the admin key (env ADMIN_KEY) and one key per station.
Station keys are stored only as a SHA-256 hash and shown once at creation."""
import hashlib
import secrets

from fastapi import HTTPException, Request

from . import config
from .db import connect


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_station_key() -> str:
    return secrets.token_urlsafe(24)


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    raise HTTPException(401, "missing bearer token")


def require_admin(request: Request) -> None:
    if not config.ADMIN_KEY:
        raise HTTPException(503, "ADMIN_KEY not configured")
    if not secrets.compare_digest(_bearer(request), config.ADMIN_KEY):
        raise HTTPException(403, "not the admin key")


def require_station(request: Request) -> dict:
    key_hash = hash_key(_bearer(request))
    with connect() as conn:
        row = conn.execute(
            "SELECT id, slug FROM stations WHERE api_key_hash = ?", (key_hash,)
        ).fetchone()
    if row is None:
        raise HTTPException(403, "unknown station key")
    return dict(row)
