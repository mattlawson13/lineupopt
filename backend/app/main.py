from __future__ import annotations

import datetime
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_backtest,
    routes_build,
    routes_imports,
    routes_lineups,
    routes_players,
    routes_resolutions,
    routes_slates,
)
from app.db.session import SessionLocal, create_all
from app.ingestion.slate_builder import capture_all_open_slates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="LineupOpt", description="DraftKings DFS projection, simulation & optimization platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    # Also allow dev-tunnel origins (localtunnel/ngrok) and the deployed
    # Vercel frontend — narrow to these specific host patterns rather than
    # a blanket wildcard. In the deployed setup the browser normally only
    # ever talks to the Vercel origin (Next.js proxies /api server-side —
    # see frontend/next.config.js), so this mainly matters if the backend
    # is ever called directly.
    allow_origin_regex=r"https://.*\.(loca\.lt|ngrok-free\.app|ngrok\.app|ngrok\.io|vercel\.app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


logger = logging.getLogger("app.capture_scheduler")

scheduler = BackgroundScheduler(timezone="UTC")


def _run_scheduled_capture() -> None:
    """Job body for the periodic capture — see capture_all_open_slates for
    why this needs to run proactively (DraftKings zeroes out salary data
    once a slate locks, so we have to grab it while the slate is still
    open). Owns its own DB session and never lets an exception escape,
    since an uncaught one would silently kill all future scheduled runs.
    """
    db = SessionLocal()
    try:
        result = capture_all_open_slates(db)
        if result["newly_captured"] or result["errors"]:
            logger.info("scheduled capture: %s", result)
    except Exception:
        logger.exception("scheduled capture_all_open_slates failed")
    finally:
        db.close()


@app.on_event("startup")
def on_startup() -> None:
    # Dev convenience — production deployments should use Alembic
    # migrations (see backend/alembic/) instead of create_all().
    create_all()

    if not scheduler.running:
        scheduler.add_job(
            _run_scheduled_capture,
            "interval",
            minutes=15,
            id="capture_open_slates",
            next_run_time=datetime.datetime.now(datetime.timezone.utc),
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


app.include_router(routes_build.router)
app.include_router(routes_slates.router)
app.include_router(routes_players.router)
app.include_router(routes_lineups.router)
app.include_router(routes_imports.router)
app.include_router(routes_backtest.router)
app.include_router(routes_resolutions.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
