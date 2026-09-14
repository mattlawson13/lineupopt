from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serialize import serialize_game, serialize_player_row, serialize_slate
from app.data_sources.base import SourceUnavailableError
from app.data_sources.draftkings import DraftKingsApiSource
from app.ingestion.injury_watch import check_injury_changes
from app.ingestion.resolution import ResolutionUnavailableError, resolve_slate
from app.ingestion.slate_builder import SlateCaptureUnavailableError, capture_all_open_slates
from app.models.analytics import OwnershipProjection, PlayerSimulationResult, SimulationRun, SlateResolution
from app.models.core import Game
from app.models.projections import EnsembleProjection
from app.models.slate import DraftKingsPlayer, Slate

router = APIRouter(prefix="/api/slates", tags=["slates"])


@router.get("")
def list_slates(db: Session = Depends(get_db)):
    slates = db.execute(select(Slate).order_by(Slate.start_time_utc.desc())).scalars().all()
    return [serialize_slate(s) for s in slates]


@router.post("/capture_all_open")
def capture_all_open_slates_route(db: Session = Depends(get_db)):
    """Saves real player/salary/game data for every DK slate currently
    open for entry that this app hasn't already captured — before it
    locks and that data becomes permanently unavailable (see
    ingestion/slate_builder.py's capture_slate_entities for why). Also
    runs automatically on a schedule (see main.py); this is the on-demand
    version of the same thing.
    """
    try:
        return capture_all_open_slates(db)
    except SlateCaptureUnavailableError as exc:
        raise HTTPException(503, str(exc))


@router.post("/check_injury_changes")
def check_injury_changes_route(db: Session = Depends(get_db)):
    """Flags any already-built, not-yet-started slate whose players'
    injury designations have changed since the last build (see
    ingestion/injury_watch.py) — never auto-rebuilds, just flags. Also
    runs automatically on a schedule (see main.py); this is the on-demand
    version of the same thing.
    """
    return check_injury_changes(db)


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


@router.get("/available/{dk_draft_group_id}/contests")
def list_contests_for_draft_group(dk_draft_group_id: str):
    """Individual contests within one draft group (unlike /available above,
    which collapses a whole group into one representative row) — lets the
    frontend offer "which specific contest are you entering?" so the build
    can calibrate its objective to that contest's real field size (see
    optimization/contest_calibration.py) rather than a generic GPP bucket.
    """
    try:
        result = DraftKingsApiSource().get_nfl_contests()
    except SourceUnavailableError as exc:
        raise HTTPException(503, str(exc))

    contests = [c for c in result.data if c.dk_draft_group_id == dk_draft_group_id]
    contests.sort(key=lambda c: c.total_prizes, reverse=True)
    return [
        {
            "dk_contest_id": c.dk_contest_id, "name": c.name, "entry_fee": c.entry_fee,
            "total_prizes": c.total_prizes, "max_entries": c.max_entries, "is_guaranteed": c.is_guaranteed,
        }
        for c in contests
    ]


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
        select(SimulationRun).where(SimulationRun.slate_id == slate_id).order_by(SimulationRun.completed_at.desc().nullslast())
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


@router.post("/{slate_id}/resolve")
def resolve_slate_route(slate_id: str, optimization_run_id: str | None = None, db: Session = Depends(get_db)):
    """Grades a slate's most recent (or a specified) optimization run
    against real DK points, once nflverse or ESPN has published that
    week's final box scores. See ingestion/resolution.py for what this
    can and can't determine (K isn't resolvable — not projected on either
    source).
    """
    slate = db.get(Slate, slate_id)
    if not slate:
        raise HTTPException(404, "Slate not found")
    try:
        result = resolve_slate(db, slate_id, optimization_run_id)
    except ResolutionUnavailableError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return dataclasses.asdict(result)


@router.get("/{slate_id}/resolutions")
def list_slate_resolutions(slate_id: str, db: Session = Depends(get_db)):
    slate = db.get(Slate, slate_id)
    if not slate:
        raise HTTPException(404, "Slate not found")
    rows = db.execute(
        select(SlateResolution).where(SlateResolution.slate_id == slate_id).order_by(SlateResolution.created_at.desc())
    ).scalars().all()
    return [
        {
            "id": r.id, "optimization_run_id": r.optimization_run_id, "retro_optimal_lineup_id": r.retro_optimal_lineup_id,
            "our_best_actual_points": r.our_best_actual_points, "retro_optimal_points": r.retro_optimal_points,
            "players_resolved": r.players_resolved, "mae": r.mae, "bias": r.bias,
            "summary": r.summary, "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.delete("/{slate_id}/resolutions/{resolution_id}")
def delete_slate_resolution(slate_id: str, resolution_id: str, db: Session = Depends(get_db)):
    """Clears a resolution so the slate can be re-resolved from scratch —
    e.g. one computed before its game(s) had actually finished (a stale
    or otherwise bad resolution would otherwise block resolve_all from
    ever retrying that slate, since it only skips slates that already
    have *a* resolution, not a *good* one).
    """
    resolution = db.get(SlateResolution, resolution_id)
    if not resolution or resolution.slate_id != slate_id:
        raise HTTPException(404, "Resolution not found")
    db.delete(resolution)
    db.commit()
    return {"deleted": resolution_id}
