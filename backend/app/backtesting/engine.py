"""Backtesting framework — spec section 23/24.

Runs the SAME projection engine used in production against historical
weeks, strictly out-of-sample (a week-N prediction only ever sees games
through week N-1 — see `through_week` usage below), then compares against
actual DK fantasy points computed from nflverse's real stat lines using
the identical `compute_dk_points` scorer the live system uses.

Known limitation (documented rather than faked, per project policy): we do
not have a legitimate free/historical source for DK salaries or Vegas
lines on old slates, so "accuracy by salary range" and "by game total"
(spec section 24) are left as `None`/omitted rather than fabricated;
"accuracy by position" and "by player type" (usage volume) are fully real.
Wire in a paid historical odds/salary archive to fill in the rest without
changing this module's shape.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from app.data_sources.base import SourceUnavailableError
from app.data_sources.nfl_stats import NFLStatsSource
from app.features.matchup import compute_matchup_zscores
from app.features.nflverse_adapter import (
    NFL_POSITION_MAP,
    build_fpts_allowed_by_team_position,
    build_game_logs_by_player,
    nflverse_row_to_game_stats,
)
from app.features.usage_features import compute_usage_snapshot
from app.normalization.player_matcher import normalize_name, normalize_team_abbreviation
from app.projections.nfl.common import PlayerProjectionContext
from app.projections.nfl.dk_points import compute_dk_points
from app.projections.nfl.model import project_player

MIN_PRIOR_GAMES = 2


@dataclasses.dataclass
class BacktestRow:
    player_name: str
    position: str
    season: int
    week: int
    predicted_points: float
    actual_points: float
    error: float  # predicted - actual
    games_sampled: int


@dataclasses.dataclass
class GroupMetrics:
    n: int
    mae: float
    rmse: float
    bias: float
    correlation: float


@dataclasses.dataclass
class BacktestSummary:
    overall: GroupMetrics
    by_position: dict[str, GroupMetrics]
    by_usage_tier: dict[str, GroupMetrics]  # "low_volume" (<3 prior games) vs "established"


def _metrics(rows: list[BacktestRow]) -> GroupMetrics:
    if not rows:
        return GroupMetrics(0, 0.0, 0.0, 0.0, 0.0)
    pred = np.array([r.predicted_points for r in rows])
    actual = np.array([r.actual_points for r in rows])
    errors = pred - actual
    corr = float(np.corrcoef(pred, actual)[0, 1]) if len(rows) > 1 and pred.std() > 0 and actual.std() > 0 else 0.0
    return GroupMetrics(
        n=len(rows),
        mae=round(float(np.abs(errors).mean()), 3),
        rmse=round(float(np.sqrt((errors**2).mean())), 3),
        bias=round(float(errors.mean()), 3),
        correlation=round(corr, 3),
    )


def run_backtest(season: int, weeks: list[int]) -> tuple[list[BacktestRow], BacktestSummary]:
    try:
        df = NFLStatsSource().get_player_stats().data
    except SourceUnavailableError as exc:
        raise RuntimeError(f"Cannot backtest — nflverse unavailable: {exc}") from exc

    df_season = df[df["season"] == season]
    if df_season.empty:
        raise RuntimeError(f"No nflverse data for season {season}")

    rows: list[BacktestRow] = []
    for week in weeks:
        game_logs = build_game_logs_by_player(df_season, season, through_week=week)
        fpts_allowed = build_fpts_allowed_by_team_position(df_season, season, through_week=week)
        matchup_z = compute_matchup_zscores(fpts_allowed) if fpts_allowed else {}

        week_df = df_season[df_season["week"] == week]
        for _, raw in week_df.iterrows():
            position = NFL_POSITION_MAP.get(raw["position"])
            if not position:
                continue
            key = (normalize_name(raw["player_display_name"]), position)
            logs = game_logs.get(key, [])
            if len(logs) < MIN_PRIOR_GAMES:
                continue

            season_usage = compute_usage_snapshot(logs)
            recent_usage = compute_usage_snapshot(logs[-3:])
            opponent = normalize_team_abbreviation(str(raw.get("opponent_team") or ""))
            team = normalize_team_abbreviation(str(raw.get("recent_team") or ""))

            ctx = PlayerProjectionContext(
                player_id=key[0], name=raw["player_display_name"], position=position, team=team, opponent=opponent,
                is_home=False, season_usage=season_usage, recent_usage=recent_usage, games_sampled=len(logs),
                implied_team_total=22.0, opponent_implied_total=22.0,  # no legitimate historical Vegas source — see module docstring
                matchup_zscore=matchup_z.get((opponent, position), 0.0),
            )
            component = project_player(ctx)
            actual_points = compute_dk_points(nflverse_row_to_game_stats(raw))

            rows.append(BacktestRow(
                player_name=raw["player_display_name"], position=position, season=season, week=week,
                predicted_points=component.projected_points, actual_points=actual_points,
                error=round(component.projected_points - actual_points, 2), games_sampled=len(logs),
            ))

    by_position = {pos: _metrics([r for r in rows if r.position == pos]) for pos in {r.position for r in rows}}
    by_usage_tier = {
        "low_volume": _metrics([r for r in rows if r.games_sampled < 3]),
        "established": _metrics([r for r in rows if r.games_sampled >= 3]),
    }
    summary = BacktestSummary(overall=_metrics(rows), by_position=by_position, by_usage_tier=by_usage_tier)
    return rows, summary
