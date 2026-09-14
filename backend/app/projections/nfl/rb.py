from __future__ import annotations

from app.projections.nfl.common import (
    ComponentProjection,
    PlayerProjectionContext,
    injury_delta,
    matchup_delta,
    snap_trend_delta,
    usage_trend_delta,
    vegas_delta,
    wind_penalty,
)
from app.projections.nfl.dk_points import compute_dk_points

VEGAS_SENSITIVITY = 0.15
SPREAD_SENSITIVITY = 0.12   # extra pts per point favored (negative spread = favorite); positive game script -> more carries late
MATCHUP_SENSITIVITY = 1.3   # rush-defense matchup


def _stat_line(usage: dict) -> dict:
    rush_att = usage.get("rush_attempts_pg", 10.0)
    rush_ypc = usage.get("rush_yards_per_carry", 4.2)
    rush_td_rate = usage.get("rush_td_rate", 0.025)
    targets = usage.get("targets_pg", 2.5)
    catch_rate = usage.get("catch_rate", 0.72)
    rec_ypc = usage.get("rec_yards_per_reception", 7.2)
    rec_td_rate = usage.get("rec_td_rate", 0.03)
    receptions = targets * catch_rate

    return {
        "rush_attempts": rush_att,
        "rush_yards": rush_att * rush_ypc,
        "rush_td": rush_att * rush_td_rate,
        "targets": targets,
        "receptions": receptions,
        "rec_yards": receptions * rec_ypc,
        "rec_td": receptions * rec_td_rate,
        "fumbles_lost": usage.get("fumbles_lost_pg", 0.08),
    }


def project(ctx: PlayerProjectionContext) -> ComponentProjection:
    season_stats = _stat_line(ctx.season_usage)
    base_points = compute_dk_points(season_stats)

    def pts_fn(usage: dict) -> float:
        return compute_dk_points(_stat_line(usage))

    vegas_adj = vegas_delta(ctx, VEGAS_SENSITIVITY) + (-ctx.spread) * SPREAD_SENSITIVITY
    usage_adj = usage_trend_delta(ctx, pts_fn)
    matchup_adj = matchup_delta(ctx, MATCHUP_SENSITIVITY)
    snap_trend_adj = snap_trend_delta(ctx, base_points)

    pre_weather_total = base_points + vegas_adj + usage_adj + matchup_adj + snap_trend_adj
    wind_factor = wind_penalty(ctx.wind_mph if not ctx.is_dome else 0.0, threshold=18.0, per_mph=0.01)  # RBs barely affected
    weather_adj = pre_weather_total * (wind_factor - 1.0)

    injury_adj = injury_delta(ctx, base_points + vegas_adj + usage_adj + matchup_adj + snap_trend_adj + weather_adj)

    final_usage = ctx.recent_usage or ctx.season_usage
    return ComponentProjection(
        player_id=ctx.player_id,
        position="RB",
        stat_line=_stat_line(final_usage),
        base_projection=base_points,
        vegas_adjustment=vegas_adj,
        usage_adjustment=usage_adj,
        matchup_adjustment=matchup_adj,
        weather_adjustment=weather_adj,
        injury_adjustment=injury_adj,
        snap_trend_adjustment=snap_trend_adj,
    )
