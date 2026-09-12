from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_backtest, routes_build, routes_imports, routes_lineups, routes_players, routes_slates
from app.db.session import create_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="LineupOpt", description="DraftKings DFS projection, simulation & optimization platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    # Dev convenience — production deployments should use Alembic
    # migrations (see backend/alembic/) instead of create_all().
    create_all()


app.include_router(routes_build.router)
app.include_router(routes_slates.router)
app.include_router(routes_players.router)
app.include_router(routes_lineups.router)
app.include_router(routes_imports.router)
app.include_router(routes_backtest.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
