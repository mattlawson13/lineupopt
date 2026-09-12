"""DST projections are structured differently from offensive positions:
the dominant driver is the OPPONENT's offense (their implied total and
turnover/sack proneness), not this team's own implied total.
"""
from __future__ import annotations

from app.projections.nfl.common import (
    LEAGUE_AVG_IMPLIED_TOTAL,
    ComponentProjection,
    PlayerProjectionContext,
    injury_delta,
    matchup_delta,
    usage_trend_delta,
)
from app.projections.nfl.dk_points import compute_dk_points

OPPONENT_TOTAL_SENSITIVITY = 0.55  # fantasy pts per point the OPPONENT's implied total is below league average
MATCHUP_SENSITIVITY = 1.4          # opponent offensive-line/turnover-proneness matchup


def _stat_line(usage: dict, opponent_implied_total: float) -> dict:
    return {
        "sacks": usage.get("sacks_pg", 2.2),
        "dst_interceptions": usage.get("int_pg", 0.8),
        "dst_fumble_recoveries": usage.get("fumble_rec_pg", 0.6),
        "dst_td": usage.get("def_td_rate", 0.12),
        "safeties": usage.get("safety_rate", 0.03),
        "blocked_kicks": usage.get("blocked_kick_rate", 0.05),
        "dst_return_td": usage.get("return_td_rate", 0.06),
        "points_allowed": usage.get("points_allowed_pg", opponent_implied_total),
    }


def project(ctx: PlayerProjectionContext) -> ComponentProjection:
    season_stats = _stat_line(ctx.season_usage, LEAGUE_AVG_IMPLIED_TOTAL)
    base_points = compute_dk_points(season_stats)

    at_actual_opponent_total = compute_dk_points(_stat_line(ctx.season_usage, ctx.opponent_implied_total))
    vegas_adj = at_actual_opponent_total - base_points

    def pts_fn(usage: dict) -> float:
        return compute_dk_points(_stat_line(usage, ctx.opponent_implied_total))

    usage_adj = usage_trend_delta(ctx, pts_fn)
    matchup_adj = matchup_delta(ctx, MATCHUP_SENSITIVITY)

    injury_adj = injury_delta(ctx, base_points + vegas_adj + usage_adj + matchup_adj)

    final_usage = ctx.recent_usage or ctx.season_usage
    return ComponentProjection(
        player_id=ctx.player_id,
        position="DST",
        stat_line=_stat_line(final_usage, ctx.opponent_implied_total),
        base_projection=base_points,
        vegas_adjustment=vegas_adj,
        usage_adjustment=usage_adj,
        matchup_adjustment=matchup_adj,
        weather_adjustment=0.0,  # weather affects the offense's output, already reflected in opponent implied total
        injury_adjustment=injury_adj,
    )
