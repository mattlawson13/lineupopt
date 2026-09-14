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

Data source: tries nflverse first (get_player_stats), then falls back to
ESPN's core API (features/espn_adapter.py) — the same source that already
backfills projections/matchup data for the current season. This matters:
nflverse has never had 2026 data at all, so without the ESPN fallback
this whole module would be permanently unusable for any slate built this
season. ESPN also has real team-defense box scores (unlike nflverse,
which is offense-only), so DST is resolved from real stats too now — not
skipped as "unresolvable" the way it used to be here.

K is still excluded (UNRESOLVABLE_POSITIONS) — this app has never
projected K at all, on either source, so there's no projection to grade
against; nothing to compare, not a data-availability gap like DST was.
"""
from __future__ import annotations

import dataclasses

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.base import SourceUnavailableError
from app.data_sources.nfl_stats import NFLStatsSource
from app.features import espn_adapter
from app.features.nflverse_adapter import nflverse_row_to_game_stats
from app.models.analytics import PlayerActualResult, SlateResolution
from app.models.core import Player
from app.models.lineup import Lineup, LineupPlayer, OptimizationRun
from app.models.projections import EnsembleProjection
from app.models.slate import DraftKingsPlayer, Slate
from app.normalization.player_matcher import normalize_name
from app.optimization.dk_rules import get_contest_rules
from app.optimization.optimizer import InfeasibleLineupError, OptimizerPlayer, optimize_single_lineup
from app.optimization.stacking import classify_stack
from app.projections.nfl.dk_points import compute_dk_points
from app.projections.nfl.dst import _stat_line as dst_stat_line

UNRESOLVABLE_POSITIONS = {"K"}  # see module docstring


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

    actual_by_norm_name: dict[str, dict] = {}
    try:
        df = NFLStatsSource().get_player_stats(slate.season).data
        week_df = df[df["week"] == slate.week]
        if week_df.empty:
            raise SourceUnavailableError(f"nflverse has no {slate.season} week {slate.week} rows yet")
        for _, raw in week_df.iterrows():
            stat_line = nflverse_row_to_game_stats(raw)
            actual_by_norm_name[normalize_name(raw["player_display_name"])] = {
                "points": compute_dk_points(stat_line), "stat_line": stat_line,
            }
    except SourceUnavailableError as nflverse_exc:
        # nflverse has never had 2026 data at all — same ESPN fallback
        # already used for projections (slate_builder.py's
        # _load_historical_stats) and matchup ratings all season.
        try:
            espn_rows = espn_adapter.fetch_week_player_rows(slate.season, slate.week)
        except espn_adapter.ESPNUnavailableError as espn_exc:
            raise ResolutionUnavailableError(
                f"Cannot resolve — neither nflverse ({nflverse_exc}) nor ESPN ({espn_exc}) has "
                f"{slate.season} week {slate.week} results yet"
            ) from espn_exc
        if not espn_rows:
            raise ResolutionUnavailableError(
                f"No {slate.season} week {slate.week} results published yet — the games may not be final"
            )
        for row in espn_rows:
            stat_line = {k: v for k, v in row.items() if k not in ("player_display_name", "position")}
            actual_by_norm_name[normalize_name(row["player_display_name"])] = {
                "points": compute_dk_points(stat_line), "stat_line": stat_line,
            }

    # DST: nflverse has never had team-defense stats at all (offense-only
    # by design) — ESPN is the only source for this regardless of which
    # source handled skill positions above, so it's always attempted
    # separately. Best-effort: a team simply stays unresolved (falls back
    # to the same "0 points" convention as any unmatched player below)
    # rather than failing the whole resolution.
    actual_dst_by_team: dict[str, dict] = {}
    try:
        dst_rows = espn_adapter.fetch_week_team_defense_rows(slate.season, slate.week)
    except espn_adapter.ESPNUnavailableError:
        dst_rows = []
    for row in dst_rows:
        usage = {
            "sacks_pg": row["sacks"], "interceptions_pg": row["interceptions"],
            "fumble_recoveries_pg": row["fumble_recoveries"], "def_td_pg": row["def_td"],
            "safety_pg": row["safety"], "blocked_kick_pg": row["blocked_kick"],
            "return_td_pg": row["return_td"], "points_allowed_pg": row["points_allowed"],
        }
        stat_line = dst_stat_line(usage, row["points_allowed"])
        actual_dst_by_team[row["team"]] = {"points": compute_dk_points(stat_line), "stat_line": stat_line}

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
        if row.dk_position == "DST":
            match = actual_dst_by_team.get(row.team_abbreviation)
        else:
            match = actual_by_norm_name.get(normalize_name(row.display_name))
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

    # K still gets 0 in the retro-optimal solve (never projected on either
    # source — see module docstring) so the solver just spends the least
    # it has to there. DST is populated for real above now.
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

        # Classified and stored (not just described) so retro-optimal
        # builds can be aggregated across slates later — see
        # GET /api/resolutions/patterns — to answer "what does a winning
        # roster actually look like" (stack shape, salary usage) from our
        # own real, resolved slates instead of needing external data.
        players_by_id = {p.player_id: p for p in optimizer_players}
        stack = classify_stack(retro.assignments, players_by_id)

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
            stack_type=stack.stack_type.value if stack.stack_type else None,
            stack_description=f"Retro-optimal: best lineup obtainable with perfect hindsight. {stack.description}",
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
