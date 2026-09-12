from __future__ import annotations

from app.projections.nfl.common import (
    ComponentProjection,
    PlayerProjectionContext,
    injury_delta,
    matchup_delta,
    usage_trend_delta,
    vegas_delta,
    wind_penalty,
)
from app.projections.nfl.dk_points import compute_dk_points

VEGAS_SENSITIVITY = 0.35   # fantasy pts per implied-total point above/below league average
MATCHUP_SENSITIVITY = 1.1  # fantasy pts per matchup z-score unit


def _stat_line(usage: dict) -> dict:
    attempts = usage.get("pass_attempts_pg", 32.0)
    comp_pct = usage.get("completion_pct", 0.64)
    ypa = usage.get("pass_yards_per_attempt", 7.0)
    pass_td_rate = usage.get("pass_td_rate", 0.045)  # TDs per attempt
    int_rate = usage.get("int_rate", 0.025)
    rush_att = usage.get("rush_attempts_pg", 2.5)
    rush_ypc = usage.get("rush_yards_per_carry", 4.4)
    rush_td_rate = usage.get("rush_td_rate", 0.02)

    return {
        "pass_attempts": attempts,
        "pass_completions": attempts * comp_pct,
        "pass_yards": attempts * ypa,
        "pass_td": attempts * pass_td_rate,
        "interceptions": attempts * int_rate,
        "rush_attempts": rush_att,
        "rush_yards": rush_att * rush_ypc,
        "rush_td": rush_att * rush_td_rate,
        "fumbles_lost": usage.get("fumbles_lost_pg", 0.12),
    }


def project(ctx: PlayerProjectionContext) -> ComponentProjection:
    season_stats = _stat_line(ctx.season_usage)
    base_points = compute_dk_points(season_stats)

    def pts_fn(usage: dict) -> float:
        return compute_dk_points(_stat_line(usage))

    vegas_adj = vegas_delta(ctx, VEGAS_SENSITIVITY)
    usage_adj = usage_trend_delta(ctx, pts_fn)
    matchup_adj = matchup_delta(ctx, MATCHUP_SENSITIVITY)

    pre_weather_total = base_points + vegas_adj + usage_adj + matchup_adj
    wind_factor = wind_penalty(ctx.wind_mph if not ctx.is_dome else 0.0)
    weather_adj = pre_weather_total * (wind_factor - 1.0)

    injury_adj = injury_delta(ctx, base_points + vegas_adj + usage_adj + matchup_adj + weather_adj)

    final_usage = ctx.recent_usage or ctx.season_usage
    return ComponentProjection(
        player_id=ctx.player_id,
        position="QB",
        stat_line=_stat_line(final_usage),
        base_projection=base_points,
        vegas_adjustment=vegas_adj,
        usage_adjustment=usage_adj,
        matchup_adjustment=matchup_adj,
        weather_adjustment=weather_adj,
        injury_adjustment=injury_adj,
    )
