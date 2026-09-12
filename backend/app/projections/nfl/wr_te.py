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

VEGAS_SENSITIVITY = {"WR": 0.28, "TE": 0.18}
MATCHUP_SENSITIVITY = {"WR": 1.0, "TE": 0.65}


def _stat_line(usage: dict) -> dict:
    targets = usage.get("targets_pg", 5.0)
    catch_rate = usage.get("catch_rate", 0.63)
    rec_ypc = usage.get("rec_yards_per_reception", 11.0)
    rec_td_rate = usage.get("rec_td_rate", 0.05)
    receptions = targets * catch_rate
    rush_att = usage.get("rush_attempts_pg", 0.0)  # jet sweeps / gadget rushes
    rush_ypc = usage.get("rush_yards_per_carry", 7.0)

    return {
        "targets": targets,
        "receptions": receptions,
        "rec_yards": receptions * rec_ypc,
        "rec_td": receptions * rec_td_rate,
        "rush_attempts": rush_att,
        "rush_yards": rush_att * rush_ypc,
        "rush_td": rush_att * usage.get("rush_td_rate", 0.02),
        "fumbles_lost": usage.get("fumbles_lost_pg", 0.02),
    }


def project(ctx: PlayerProjectionContext) -> ComponentProjection:
    position = ctx.position  # "WR" or "TE"
    season_stats = _stat_line(ctx.season_usage)
    base_points = compute_dk_points(season_stats)

    def pts_fn(usage: dict) -> float:
        return compute_dk_points(_stat_line(usage))

    vegas_adj = vegas_delta(ctx, VEGAS_SENSITIVITY.get(position, 0.25))
    usage_adj = usage_trend_delta(ctx, pts_fn)
    matchup_adj = matchup_delta(ctx, MATCHUP_SENSITIVITY.get(position, 0.9))

    pre_weather_total = base_points + vegas_adj + usage_adj + matchup_adj
    wind_factor = wind_penalty(ctx.wind_mph if not ctx.is_dome else 0.0)
    weather_adj = pre_weather_total * (wind_factor - 1.0)

    injury_adj = injury_delta(ctx, base_points + vegas_adj + usage_adj + matchup_adj + weather_adj)

    final_usage = ctx.recent_usage or ctx.season_usage
    return ComponentProjection(
        player_id=ctx.player_id,
        position=position,
        stat_line=_stat_line(final_usage),
        base_projection=base_points,
        vegas_adjustment=vegas_adj,
        usage_adjustment=usage_adj,
        matchup_adjustment=matchup_adj,
        weather_adjustment=weather_adj,
        injury_adjustment=injury_adj,
    )
