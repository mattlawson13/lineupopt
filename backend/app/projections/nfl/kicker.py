from __future__ import annotations

from app.projections.nfl.common import (
    LEAGUE_AVG_IMPLIED_TOTAL,
    ComponentProjection,
    PlayerProjectionContext,
    injury_delta,
    usage_trend_delta,
    wind_penalty,
)
from app.projections.nfl.dk_points import compute_dk_points

DEFAULT_DISTANCE_SPLIT = {"0_39": 0.45, "40_49": 0.35, "50_plus": 0.20}


def _stat_line(usage: dict, implied_total: float) -> dict:
    fg_att = usage.get("fg_attempts_pg", 1.7)
    xp_att = usage.get("xp_attempts_pg", max(implied_total / 7.0 * 0.6, 0.5))
    make_rate = usage.get("fg_make_rate", 0.86)
    split = usage.get("fg_distance_split", DEFAULT_DISTANCE_SPLIT)
    made = fg_att * make_rate

    return {
        "fg_0_39": made * split["0_39"],
        "fg_40_49": made * split["40_49"],
        "fg_50_plus": made * split["50_plus"],
        "fg_missed": fg_att - made,
        "xp_made": xp_att * 0.94,
    }


def project(ctx: PlayerProjectionContext) -> ComponentProjection:
    season_stats = _stat_line(ctx.season_usage, LEAGUE_AVG_IMPLIED_TOTAL)
    base_points = compute_dk_points(season_stats)

    at_own_total = compute_dk_points(_stat_line(ctx.season_usage, ctx.implied_team_total))
    vegas_adj = at_own_total - base_points  # team scoring expectation directly drives XP/FG opportunity

    def pts_fn(usage: dict) -> float:
        return compute_dk_points(_stat_line(usage, ctx.implied_team_total))

    usage_adj = usage_trend_delta(ctx, pts_fn)

    pre_weather_total = base_points + vegas_adj + usage_adj
    # Long FGs and accuracy both suffer in wind; kickers are the position
    # most sensitive to it (spec section 7: "Wind should have a meaningful
    # effect on ... kicking projections").
    wind_factor = wind_penalty(ctx.wind_mph if not ctx.is_dome else 0.0, threshold=10.0, per_mph=0.025)
    weather_adj = pre_weather_total * (wind_factor - 1.0)

    injury_adj = injury_delta(ctx, base_points + vegas_adj + usage_adj + weather_adj)

    final_usage = ctx.recent_usage or ctx.season_usage
    return ComponentProjection(
        player_id=ctx.player_id,
        position="K",
        stat_line=_stat_line(final_usage, ctx.implied_team_total),
        base_projection=base_points,
        vegas_adjustment=vegas_adj,
        usage_adjustment=usage_adj,
        matchup_adjustment=0.0,  # matchup has negligible effect for kickers beyond team total, already captured above
        weather_adjustment=weather_adj,
        injury_adjustment=injury_adj,
    )
