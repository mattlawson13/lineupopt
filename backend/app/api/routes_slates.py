from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serialize import serialize_game, serialize_player_row, serialize_slate
from app.models.analytics import OwnershipProjection, PlayerSimulationResult, SimulationRun
from app.models.core import Game
from app.models.projections import EnsembleProjection
from app.models.slate import DraftKingsPlayer, Slate

router = APIRouter(prefix="/api/slates", tags=["slates"])


@router.get("")
def list_slates(db: Session = Depends(get_db)):
    slates = db.execute(select(Slate).order_by(Slate.start_time_utc.desc())).scalars().all()
    return [serialize_slate(s) for s in slates]


@router.get("/{slate_id}")
def get_slate(slate_id: str, db: Session = Depends(get_db)):
    slate = db.get(Slate, slate_id)
    if not slate:
        raise HTTPException(404, "Slate not found")
    games = [db.get(Game, gid) for gid in slate.game_ids]
    return {
        **serialize_slate(slate),
        "games": [serialize_game(g) for g in games if g],
    }


@router.get("/{slate_id}/games")
def get_slate_games(slate_id: str, db: Session = Depends(get_db)):
    slate = db.get(Slate, slate_id)
    if not slate:
        raise HTTPException(404, "Slate not found")
    games = [db.get(Game, gid) for gid in slate.game_ids]
    return [serialize_game(g) for g in games if g]


@router.get("/{slate_id}/players")
def get_slate_players(slate_id: str, db: Session = Depends(get_db)):
    slate = db.get(Slate, slate_id)
    if not slate:
        raise HTTPException(404, "Slate not found")

    dk_rows = db.execute(select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate_id)).scalars().all()
    # Ordered ascending so that, when a slate has been built more than
    # once, the LATEST run's row wins the dict (multiple rows per player
    # are expected across model versions — see models/projections.py).
    ensembles = {
        e.player_id: e
        for e in db.execute(select(EnsembleProjection).where(EnsembleProjection.slate_id == slate_id).order_by(EnsembleProjection.created_at)).scalars().all()
    }
    ownerships = {
        o.player_id: o
        for o in db.execute(select(OwnershipProjection).where(OwnershipProjection.slate_id == slate_id).order_by(OwnershipProjection.created_at)).scalars().all()
    }
    latest_sim = db.execute(
        select(SimulationRun).where(SimulationRun.slate_id == slate_id).order_by(SimulationRun.completed_at.desc())
    ).scalars().first()
    sim_by_player = {}
    if latest_sim:
        sim_by_player = {
            r.player_id: r
            for r in db.execute(select(PlayerSimulationResult).where(PlayerSimulationResult.simulation_run_id == latest_sim.id)).scalars().all()
        }

    return [
        serialize_player_row(row, ensembles.get(row.player_id), ownerships.get(row.player_id), sim_by_player.get(row.player_id))
        for row in dk_rows
        if row.player_id
    ]
