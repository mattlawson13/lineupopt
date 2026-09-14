"""Lineup listing/detail, and the manual lineup builder (spec section 21):
lock/exclude/force-stack/exposure/ownership constraints re-run against a
slate's already-computed projections/ownership/simulation — no need to
redo data ingestion or Monte Carlo simulation for a "regenerate with these
tweaks" request.
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.lineup_evaluator import evaluate_and_rank_lineups
from app.api.deps import get_db
from app.api.schemas import ManualOptimizeRequest
from app.api.serialize import serialize_lineup
from app.config.loader import get_optimization_settings
from app.ingestion.slate_builder import _compute_correlation_scores, _resolve_contest_calibration
from app.models.analytics import Correlation, OwnershipProjection, PlayerSimulationResult, SimulationRun
from app.models.context_data import BettingLine
from app.models.core import Game, Player
from app.models.lineup import Lineup, LineupPlayer, OptimizationRun
from app.models.projections import EnsembleProjection, Projection
from app.models.slate import DraftKingsPlayer, Slate
from app.optimization.diversification import DiversificationSettings, generate_portfolio
from app.optimization.dk_rules import get_contest_rules
from app.optimization.optimizer import OptimizerPlayer
from app.optimization.stacking import classify_stack

router = APIRouter(prefix="/api/lineups", tags=["lineups"])


def _players_by_id_lookup(db: Session, lineup: Lineup) -> dict:
    out = {}
    for lp in lineup.players:
        p = db.get(Player, lp.player_id)
        if p:
            out[lp.player_id] = {"name": p.full_name, "position": p.position, "team": p.team.abbreviation if p.team else None}
    return out


@router.get("")
def list_lineups(slate_id: str, optimization_run_id: str | None = None, db: Session = Depends(get_db)):
    query = select(Lineup).where(Lineup.slate_id == slate_id)
    if optimization_run_id:
        query = query.where(Lineup.optimization_run_id == optimization_run_id)
    else:
        latest_run = db.execute(
            select(OptimizationRun).where(OptimizationRun.slate_id == slate_id).order_by(OptimizationRun.completed_at.desc())
        ).scalars().first()
        if latest_run:
            query = query.where(Lineup.optimization_run_id == latest_run.id)
    lineups = db.execute(query.order_by(Lineup.ai_rank)).scalars().all()
    return [serialize_lineup(lu, _players_by_id_lookup(db, lu)) for lu in lineups]


@router.get("/{lineup_id}")
def get_lineup(lineup_id: str, db: Session = Depends(get_db)):
    lineup = db.get(Lineup, lineup_id)
    if not lineup:
        raise HTTPException(404, "Lineup not found")
    return serialize_lineup(lineup, _players_by_id_lookup(db, lineup))


@router.post("/generate")
def generate_lineups(req: ManualOptimizeRequest, db: Session = Depends(get_db)):
    slate = db.get(Slate, req.slate_id)
    if not slate:
        raise HTTPException(404, "Slate not found")

    dk_rows = db.execute(select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == req.slate_id)).scalars().all()
    ensembles = {e.player_id: e for e in db.execute(select(EnsembleProjection).where(EnsembleProjection.slate_id == req.slate_id)).scalars().all()}
    # Ordered ascending so the LATEST build's row wins — see the same
    # pattern in get_slate_players() (routes_slates.py) for why (a
    # rebuilt slate leaves earlier Projection rows in place).
    depth_multipliers = {
        p.player_id: p.inputs.get("depth_chart_multiplier", 1.0)
        for p in db.execute(select(Projection).where(Projection.slate_id == req.slate_id).order_by(Projection.created_at)).scalars().all()
    }
    ownerships = {o.player_id: o for o in db.execute(select(OwnershipProjection).where(OwnershipProjection.slate_id == req.slate_id)).scalars().all()}
    latest_sim = db.execute(
        select(SimulationRun).where(SimulationRun.slate_id == req.slate_id).order_by(SimulationRun.completed_at.desc())
    ).scalars().first()
    sim_by_player = {}
    if latest_sim:
        sim_by_player = {
            r.player_id: r for r in db.execute(
                select(PlayerSimulationResult).where(PlayerSimulationResult.simulation_run_id == latest_sim.id)
            ).scalars().all()
        }
    if not ensembles:
        raise HTTPException(400, "Slate has no projections yet — run /api/slates/build first")

    opt_cfg = get_optimization_settings()
    mode_cfg = opt_cfg["contest_modes"].get(req.objective, opt_cfg["contest_modes"]["large_field_gpp"])
    calibrated_weights, calibration_note = _resolve_contest_calibration(req.objective, req.dk_contest_id)
    weights = calibrated_weights or mode_cfg["objective_weights"]

    correlation_rows = db.execute(select(Correlation).where(Correlation.slate_id == req.slate_id)).scalars().all()
    correlation_scores = _compute_correlation_scores(correlation_rows, {row.player_id for row in dk_rows if row.player_id})
    CORRELATION_SCALE = 15.0

    optimizer_players = []
    for row in dk_rows:
        ens = ensembles.get(row.player_id)
        if not ens or not row.game_id or not row.player_id:
            continue
        if req.min_projection and ens.median < req.min_projection:
            continue
        own = ownerships.get(row.player_id)
        if req.max_ownership_pct and own and own.projected_ownership_pct > req.max_ownership_pct:
            continue
        sim = sim_by_player.get(row.player_id)
        own_pct = own.projected_ownership_pct if own else 10.0
        leverage_proxy = max(0.0, (sim.prob_top5pct * 100 if sim else 0) - own_pct)
        # See the matching comment in slate_builder.py's _optimize_lineups:
        # the correlation matrix doesn't know about depth-chart status, so
        # this term needs the same backup discount everything else already
        # gets implicitly through ens.*/sim.* being built off a discounted
        # projection — otherwise a cheap backup QB's objective_value gets
        # inflated purely from being "connected" to many teammates.
        depth_multiplier = depth_multipliers.get(row.player_id, 1.0)
        objective_value = (
            weights.get("median", 0) * ens.median + weights.get("projection", 0) * ens.ensemble_projection
            + weights.get("ceiling", 0) * (sim.ceiling if sim else ens.ceiling) + weights.get("floor", 0) * ens.floor
            + weights.get("leverage", 0) * leverage_proxy + weights.get("volatility", 0) * (sim.std_dev if sim else ens.std_dev)
            + weights.get("correlation", 0) * correlation_scores.get(row.player_id, 0.0) * CORRELATION_SCALE * depth_multiplier
        )
        salary = row.salaries[-1].salary if row.salaries else 0
        optimizer_players.append(OptimizerPlayer(
            player_id=row.player_id, position=row.dk_position, team=row.team_abbreviation, game_id=row.game_id,
            salary=salary, objective_value=round(objective_value, 3),
            locked=row.player_id in req.locked_player_ids, excluded=row.player_id in req.excluded_player_ids,
        ))

    rules = get_contest_rules("nfl", slate.contest_type)
    diversification = DiversificationSettings(**opt_cfg["default_diversification"])
    if req.max_player_exposure_pct is not None:
        diversification.max_player_exposure_pct = req.max_player_exposure_pct
    if req.max_lineup_overlap is not None:
        diversification.max_lineup_overlap = req.max_lineup_overlap

    opt_run = OptimizationRun(
        slate_id=slate.id, simulation_run_id=latest_sim.id if latest_sim else None, objective=req.objective,
        num_lineups_requested=req.num_lineups,
        settings={"weights": weights, "contest_calibration": calibration_note, "manual": req.model_dump()},
        status="running",
    )
    db.add(opt_run)
    db.flush()

    # Same forced-stack rotation the main build pipeline uses (spec
    # section 16) — without this, this manual/filtered regenerate path
    # leaves stacking to emerge from the correlation objective weight
    # alone, which reliably under-selects it and produces "naked QB"
    # lineups (no correlated pass-catcher at all) even in GPP modes.
    stack_teams: list[str] | None = None
    if weights.get("correlation", 0) > 0:
        game_ids = {row.game_id for row in dk_rows if row.game_id}
        betting_lines = {
            bl.game_id: bl for bl in db.execute(select(BettingLine).where(BettingLine.game_id.in_(game_ids))).scalars().all()
        }
        games_by_id = {g.id: g for g in db.execute(select(Game).where(Game.id.in_(game_ids))).scalars().all()}
        team_totals: dict[str, float] = {}
        for row in dk_rows:
            if row.dk_position != "QB" or not row.game_id or row.player_id not in ensembles:
                continue
            bl = betting_lines.get(row.game_id)
            game = games_by_id.get(row.game_id)
            if not bl or not game:
                continue
            is_home = game.home_team.abbreviation == row.team_abbreviation
            team_totals[row.team_abbreviation] = bl.implied_total_home if is_home else bl.implied_total_away
        stack_teams = [t for t, _ in sorted(team_totals.items(), key=lambda kv: kv[1], reverse=True)] or None

    seed_exclude_lineups = None
    if req.exclude_lineup_ids:
        existing_lineups = db.execute(
            select(Lineup).where(Lineup.id.in_(req.exclude_lineup_ids))
        ).scalars().all()
        seed_exclude_lineups = [{lp.player_id for lp in lu.players} for lu in existing_lineups]

    portfolio = generate_portfolio(
        optimizer_players, rules, req.num_lineups, diversification,
        forced_team_min_counts=req.forced_team_min_counts or None,
        forced_qb_stack_teams=stack_teams,
        randomness_pct=mode_cfg.get("randomness_pct", 0.0), seed=req.seed,
        seed_exclude_lineups=seed_exclude_lineups,
    )

    players_by_id = {p.player_id: p for p in optimizer_players}
    lineups = []
    for lu in portfolio.lineups:
        stack = classify_stack(lu.assignments, players_by_id)
        ens_projs = [ensembles[a.player_id] for a in lu.assignments]
        lineup = Lineup(
            optimization_run_id=opt_run.id, slate_id=slate.id, salary_used=lu.salary_used,
            salary_remaining=rules.salary_cap - lu.salary_used,
            projected_points=round(sum(e.ensemble_projection for e in ens_projs), 2),
            ceiling=round(sum(e.ceiling for e in ens_projs), 2), floor=round(sum(e.floor for e in ens_projs), 2),
            stack_type=stack.stack_type.value if stack.stack_type else None, stack_description=stack.description,
        )
        db.add(lineup)
        db.flush()
        for a in lu.assignments:
            db.add(LineupPlayer(lineup_id=lineup.id, player_id=a.player_id, roster_slot=a.slot, salary=a.salary, projected_points=a.objective_value))
        lineups.append(lineup)

    opt_run.num_lineups_generated = len(lineups)
    opt_run.status = "completed"
    db.flush()
    evaluate_and_rank_lineups(db, lineups, ownerships)
    db.commit()

    return {
        "optimization_run_id": opt_run.id,
        "warnings": portfolio.warnings,
        "contest_calibration": calibration_note,
        "lineups": [serialize_lineup(lu, _players_by_id_lookup(db, lu)) for lu in lineups],
    }


@router.post("/{lineup_id}/late-swap")
def late_swap_lineup(lineup_id: str, db: Session = Depends(get_db)):
    """DK slates stagger kickoffs (early/late/SNF/MNF windows); once a
    game has kicked off you can no longer swap a player out of — or into —
    it, but everything in a game that hasn't started yet is still fair
    game, and news (inactives, last-minute scratches) keeps landing right
    up to those later kickoffs. This re-optimizes ONE existing lineup with
    that rule enforced automatically:
      - every rostered player whose game has already started is locked in
        exactly as-is (can't be swapped out)
      - every OTHER player whose game has already started is barred from
        being newly added (can't swap someone in from a game already
        under way)
      - everyone else is fair game, re-optimized against whatever the
        latest projections/ownership/simulation for this slate currently
        say (a rebuild since the original lineup was made will already be
        reflected here — this doesn't re-run ingestion itself)

    Reuses generate_lineups()'s exact machinery via a synthetic
    ManualOptimizeRequest — the only new logic here is computing the
    lock/exclude sets from kickoff times instead of a person supplying
    them by hand.
    """
    lineup = db.get(Lineup, lineup_id)
    if not lineup:
        raise HTTPException(404, "Lineup not found")
    run = db.get(OptimizationRun, lineup.optimization_run_id)
    slate = db.get(Slate, lineup.slate_id)
    if not slate:
        raise HTTPException(404, "Slate not found")

    dk_rows = db.execute(select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate.id)).scalars().all()
    game_ids = {row.game_id for row in dk_rows if row.game_id}
    games_by_id = {g.id: g for g in db.execute(select(Game).where(Game.id.in_(game_ids))).scalars().all()}
    now = datetime.datetime.now(datetime.timezone.utc)

    def _is_started(kickoff: datetime.datetime) -> bool:
        # kickoff_utc is stored naive (assumed UTC by convention, per its
        # name) in this app's usual path, but tolerate an already-aware
        # value too rather than assuming and risking a double-offset.
        aware = kickoff if kickoff.tzinfo is not None else kickoff.replace(tzinfo=datetime.timezone.utc)
        return aware <= now

    started_game_ids = {gid for gid, g in games_by_id.items() if _is_started(g.kickoff_utc)}

    if not started_game_ids:
        raise HTTPException(400, "No games in this slate have started yet — nothing to late-swap")

    dk_row_by_player_id = {row.player_id: row for row in dk_rows if row.player_id}
    current_player_ids = {lp.player_id for lp in lineup.players}

    locked_player_ids = [
        pid for pid in current_player_ids
        if (row := dk_row_by_player_id.get(pid)) and row.game_id in started_game_ids
    ]
    if len(locked_player_ids) == len(current_player_ids):
        raise HTTPException(400, "Every game in this lineup has already started — nothing left to swap")

    excluded_player_ids = [
        row.player_id for row in dk_rows
        if row.player_id and row.game_id in started_game_ids and row.player_id not in current_player_ids
    ]

    # Carry forward the original build's contest calibration (if any) so a
    # late swap stays optimized for the same specific contest rather than
    # silently reverting to the generic objective bucket.
    original_dk_contest_id = (run.settings or {}).get("dk_contest_id") if run else None

    req = ManualOptimizeRequest(
        slate_id=slate.id, num_lineups=1, objective=run.objective if run else "large_field_gpp",
        dk_contest_id=original_dk_contest_id,
        locked_player_ids=locked_player_ids, excluded_player_ids=excluded_player_ids,
    )
    result = generate_lineups(req, db)
    return {
        **result,
        "kept_from_original": len(locked_player_ids),
        "swapped_slots": len(current_player_ids) - len(locked_player_ids),
    }
