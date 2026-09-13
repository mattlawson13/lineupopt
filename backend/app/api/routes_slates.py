from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serialize import serialize_game, serialize_player_row, serialize_slate
from app.data_sources.base import SourceUnavailableError
from app.data_sources.draftkings import DraftKingsApiSource
from app.models.analytics import OwnershipProjection, PlayerSimulationResult, SimulationRun
from app.models.core import Game
from app.models.projections import EnsembleProjection
from app.models.slate import DraftKingsPlayer, Slate

router = APIRouter(prefix="/api/slates", tags=["slates"])


@router.get("")
def list_slates(db: Session = Depends(get_db)):
    slates = db.execute(select(Slate).order_by(Slate.start_time_utc.desc())).scalars().all()
    return [serialize_slate(s) for s in slates]


_SUPPORTED_DK_GAME_TYPES = {
    "Classic": "classic",
    "Showdown Captain Mode": "showdown",
}


@router.get("/available")
def list_available_dk_slates():
    """Live DraftKings NFL contest lobby, collapsed into one row per DK
    draft group — this is what lets the frontend offer a "pick a slate"
    list instead of requiring a hand-typed draft group id. One draft group
    backs many contests (cash games, GPPs, single-entry, ...) that all
    share the exact same slate of players/games, so contests are grouped
    by dk_draft_group_id, using whichever contest in the group has the
    largest total prize pool as the representative name (typically the
    flagship GPP, which is the one a person actually recognizes).

    Includes both Classic (9-man salary-cap) and Showdown Captain Mode
    (single-game, CPT + 5 FLEX) slates — the optimizer supports both
    roster formats (see dk_rules.py / dk_roster_rules_nfl.yaml). Excludes
    the other game types DK's NFL lobby also mixes in (Snake Showdown,
    Single Stat, In-Game, Madden, Best Ball, ...), which use fundamentally
    different formats this app doesn't build for.
    """
    try:
        result = DraftKingsApiSource().get_nfl_contests()
    except SourceUnavailableError as exc:
        raise HTTPException(503, str(exc))

    by_group: dict[str, dict] = {}
    for c in result.data:
        contest_format = _SUPPORTED_DK_GAME_TYPES.get(c.game_type)
        if not contest_format:
            continue
        start_iso = c.start_time_utc.isoformat()
        group = by_group.get(c.dk_draft_group_id)
        if group is None:
            group = {
                "dk_draft_group_id": c.dk_draft_group_id,
                "contest_format": contest_format,
                "start_time_utc": start_iso,
                "contest_count": 0,
                "sample_contest_name": c.name,
                "_max_prizes": c.total_prizes,
            }
            by_group[c.dk_draft_group_id] = group
        group["contest_count"] += 1
        if start_iso < group["start_time_utc"]:
            group["start_time_utc"] = start_iso
        if c.total_prizes > group["_max_prizes"]:
            group["_max_prizes"] = c.total_prizes
            group["sample_contest_name"] = c.name

    slates = sorted(by_group.values(), key=lambda g: g["start_time_utc"])
    for g in slates:
        del g["_max_prizes"]
    return slates


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
