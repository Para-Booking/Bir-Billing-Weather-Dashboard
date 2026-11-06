import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import config, jobs
from .db import init_db
from .health import router as health_router
from .ingest import router as ingest_router
from .query import router as query_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    stop = jobs.start() if config.JOBS_ENABLED else None
    yield
    if stop:
        stop.set()


app = FastAPI(title="Bir Billing Wind", lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json", redoc_url=None)
app.include_router(ingest_router)
app.include_router(query_router)
app.include_router(health_router)


@app.get("/api/health")
def health():
    return {"ok": True}


if config.SERVE_WEB:
    # Local development only; in production Caddy serves these.
    from fastapi.staticfiles import StaticFiles

    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=config.MEDIA_DIR), name="media")

    @app.get("/s/{slug}")
    def station_page(slug: str):
        return FileResponse(config.WEB_DIR / "station.html")

    @app.get("/fly")
    def fly_page():
        return FileResponse(config.WEB_DIR / "fly.html")

    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
