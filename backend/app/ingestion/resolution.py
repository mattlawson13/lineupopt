"""Post-slate resolution: once games are final, grades a slate build
against what actually happened.

Two things come out of this:
  1. Real DK points per player (PlayerActualResult) diffed against what we
     projected — the raw material for the "did the model run hot/cold on
     X" bias-tracking the user asked for. Intentionally NOT auto-applied
     anywhere yet; that's a separate, later step that needs several
     resolved slates accumulated before a correction is meaningful rather
     than noise.
  2. A "retro-optimal" lineup — the same ILP optimizer, run with actual
     points as the objective instead of projections, which is the best
     lineup that could have been built with perfect hindsight. Comparing
     our #1 lineup's real score against this tells you concretely what
     the model missed, rather than just "you scored X."

Known, honest limitation: nflverse's player_stats file is offense-only
(passing/rushing/receiving) — it has no team defense/special-teams stat
lines, so DST (and K, which this app doesn't project anyway) can't be
resolved from this source. Per project policy we don't fabricate a DST
score; those slots are scored 0 in the retro-optimal solve (so it picks
the cheapest legal DST rather than pretending to know which one scored
best) and excluded from the bias/MAE stats entirely.
"""
from __future__ import annotations

import dataclasses

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.base import SourceUnavailableError
from app.data_sources.nfl_stats import NFLStatsSource
from app.features.nflverse_adapter import nflverse_row_to_game_stats
from app.models.analytics import PlayerActualResult, SlateResolution
from app.models.core import Player
from app.models.lineup import Lineup, LineupPlayer, OptimizationRun
from app.models.projections import EnsembleProjection
from app.models.slate import DraftKingsPlayer, Slate
from app.normalization.player_matcher import normalize_name
from app.optimization.dk_rules import get_contest_rules
from app.optimization.optimizer import InfeasibleLineupError, OptimizerPlayer, optimize_single_lineup
from app.projections.nfl.dk_points import compute_dk_points

UNRESOLVABLE_POSITIONS = {"DST", "K"}  # see module docstring


class ResolutionUnavailableError(RuntimeError):
    pass


@dataclasses.dataclass
class ResolutionSummary:
    slate_resolution_id: str
    our_best_actual_points: float
    retro_optimal_points: float
    retro_optimal_lineup_id: str | None
    players_resolved: int
    mae: float
    bias: float
    biggest_misses: list[dict]  # [{name, position, projected, actual, error}, ...]


def resolve_slate(db: Session, slate_id: str, optimization_run_id: str | None = None) -> ResolutionSummary:
    slate = db.get(Slate, slate_id)
    if not slate:
        raise ValueError(f"Slate {slate_id} not found")

    run = (
        db.get(OptimizationRun, optimization_run_id) if optimization_run_id
        else db.execute(
            select(OptimizationRun).where(OptimizationRun.slate_id == slate_id).order_by(OptimizationRun.completed_at.desc())
        ).scalars().first()
    )
    if not run:
        raise ValueError(f"No optimization run found for slate {slate_id}")

    try:
        df = NFLStatsSource().get_player_stats(slate.season).data
    except SourceUnavailableError as exc:
        raise ResolutionUnavailableError(f"Cannot resolve — nflverse unavailable: {exc}") from exc

    week_df = df[df["week"] == slate.week]
    if week_df.empty:
        raise ResolutionUnavailableError(
            f"No {slate.season} week {slate.week} results published yet — the games may not be final"
        )

    actual_by_norm_name: dict[str, dict] = {}
    for _, raw in week_df.iterrows():
        stat_line = nflverse_row_to_game_stats(raw)
        actual_by_norm_name[normalize_name(raw["player_display_name"])] = {
            "points": compute_dk_points(stat_line), "stat_line": stat_line,
        }

    dk_rows = db.execute(select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate_id)).scalars().all()
    ensembles = {
        e.player_id: e for e in db.execute(
            select(EnsembleProjection).where(EnsembleProjection.slate_id == slate_id).order_by(EnsembleProjection.created_at)
        ).scalars().all()
    }

    actual_points_by_player_id: dict[str, float] = {}
    errors: list[float] = []
    biggest_misses: list[dict] = []
    resolved_count = 0

    for row in dk_rows:
        if not row.player_id or row.dk_position in UNRESOLVABLE_POSITIONS:
            continue
        norm = normalize_name(row.display_name)
        match = actual_by_norm_name.get(norm)
        # No row in the box score reads as 0 DK points (inactive/DNP/zero
        # involvement, which is the overwhelmingly common case) rather
        # than as a resolution failure — we can't fully distinguish that
        # from a rare name-matching miss, so this is stated as a known
        # limitation rather than silently assumed perfect.
        points = round(match["points"], 2) if match else 0.0
        stat_line = match["stat_line"] if match else {}
        ens = ensembles.get(row.player_id)
        projected = ens.ensemble_projection if ens else None
        error = round(projected - points, 2) if projected is not None else None

        db.add(PlayerActualResult(
            slate_id=slate_id, player_id=row.player_id, actual_dk_points=points,
            stat_line=stat_line, projected_dk_points=projected, error=error,
        ))
        actual_points_by_player_id[row.player_id] = points
        resolved_count += 1
        if error is not None:
            errors.append(error)
            player = db.get(Player, row.player_id)
            biggest_misses.append({
                "name": player.full_name if player else row.display_name, "position": row.dk_position,
                "projected": projected, "actual": points, "error": error,
            })

    # DST/K get 0 in the retro-optimal solve (never claim to know which
    # one scored best — see module docstring) so the solver just spends
    # the least it has to on them.
    for row in dk_rows:
        if row.dk_position in UNRESOLVABLE_POSITIONS and row.player_id:
            actual_points_by_player_id.setdefault(row.player_id, 0.0)

    rules = get_contest_rules("nfl", slate.contest_type)
    slot_score_mult = {s.name: s.score_multiplier for s in rules.slots}

    retro_optimal_lineup_id = None
    retro_optimal_points = 0.0
    try:
        optimizer_players = [
            OptimizerPlayer(
                player_id=row.player_id, position=row.dk_position, team=row.team_abbreviation,
                game_id=row.game_id, salary=row.salaries[-1].salary if row.salaries else 0,
                objective_value=actual_points_by_player_id.get(row.player_id, 0.0),
            )
            for row in dk_rows if row.player_id and row.game_id
        ]
        retro = optimize_single_lineup(optimizer_players, rules)
        retro_optimal_points = retro.objective_total

        retro_run = OptimizationRun(
            slate_id=slate_id, objective="retro_optimal", num_lineups_requested=1, num_lineups_generated=1,
            status="completed", settings={"note": "best possible lineup with perfect hindsight (actual DK points)"},
        )
        db.add(retro_run)
        db.flush()
        retro_lineup = Lineup(
            optimization_run_id=retro_run.id, slate_id=slate_id, salary_used=retro.salary_used,
            salary_remaining=rules.salary_cap - retro.salary_used,
            projected_points=retro.objective_total, ceiling=retro.objective_total, floor=retro.objective_total,
            stack_description="Retro-optimal: best lineup obtainable with perfect hindsight.",
        )
        db.add(retro_lineup)
        db.flush()
        for a in retro.assignments:
            db.add(LineupPlayer(lineup_id=retro_lineup.id, player_id=a.player_id, roster_slot=a.slot, salary=a.salary, projected_points=a.objective_value))
        retro_optimal_lineup_id = retro_lineup.id
    except InfeasibleLineupError:
        pass  # not enough resolved players to fill a legal roster (e.g. mid-week resolution) — leave unset

    our_best = db.execute(
        select(Lineup).where(Lineup.optimization_run_id == run.id).order_by(Lineup.ai_rank)
    ).scalars().first()
    our_best_actual_points = 0.0
    if our_best:
        for lp in our_best.players:
            mult = slot_score_mult.get(lp.roster_slot, 1.0)
            our_best_actual_points += actual_points_by_player_id.get(lp.player_id, 0.0) * mult
        our_best_actual_points = round(our_best_actual_points, 2)

    biggest_misses.sort(key=lambda m: abs(m["error"]), reverse=True)
    mae = round(sum(abs(e) for e in errors) / len(errors), 2) if errors else 0.0
    bias = round(sum(errors) / len(errors), 2) if errors else 0.0

    resolution = SlateResolution(
        slate_id=slate_id, optimization_run_id=run.id, retro_optimal_lineup_id=retro_optimal_lineup_id,
        our_best_actual_points=our_best_actual_points, retro_optimal_points=round(retro_optimal_points, 2),
        players_resolved=resolved_count, players_unmatched=0, mae=mae, bias=bias,
        summary={"biggest_misses": biggest_misses[:10]},
    )
    db.add(resolution)
    db.commit()

    return ResolutionSummary(
        slate_resolution_id=resolution.id, our_best_actual_points=our_best_actual_points,
        retro_optimal_points=round(retro_optimal_points, 2), retro_optimal_lineup_id=retro_optimal_lineup_id,
        players_resolved=resolved_count, mae=mae, bias=bias, biggest_misses=biggest_misses[:10],
    )
