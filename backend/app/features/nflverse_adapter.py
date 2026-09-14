"""Maps nflverse's `player_stats.csv` schema into the internal per-game
stat-line keys used by projections/nfl/dk_points.py and
features/usage_features.py, and builds the "fantasy points allowed by
position" matchup proxy consumed by features/matchup.py.
"""
from __future__ import annotations

import pandas as pd

from app.normalization.player_matcher import normalize_name, normalize_team_abbreviation

NFL_POSITION_MAP = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}


def nflverse_row_to_game_stats(row: pd.Series) -> dict:
    fumbles_lost = (
        (row.get("sack_fumbles_lost") or 0)
        + (row.get("rushing_fumbles_lost") or 0)
        + (row.get("receiving_fumbles_lost") or 0)
    )
    return {
        "pass_attempts": row.get("attempts") or 0,
        "pass_completions": row.get("completions") or 0,
        "pass_yards": row.get("passing_yards") or 0,
        "pass_td": row.get("passing_tds") or 0,
        "interceptions": row.get("interceptions") or 0,
        "rush_attempts": row.get("carries") or 0,
        "rush_yards": row.get("rushing_yards") or 0,
        "rush_td": row.get("rushing_tds") or 0,
        "targets": row.get("targets") or 0,
        "receptions": row.get("receptions") or 0,
        "rec_yards": row.get("receiving_yards") or 0,
        "rec_td": row.get("receiving_tds") or 0,
        "fumbles_lost": fumbles_lost,
        "fantasy_points_ppr": row.get("fantasy_points_ppr") or 0,
        "opponent_team": normalize_team_abbreviation(str(row.get("opponent_team") or "")),
        "team": normalize_team_abbreviation(str(row.get("recent_team") or "")),
        "week": row.get("week"),
        "season": row.get("season"),
    }


def build_snap_pct_lookup(df: pd.DataFrame, season: int, through_week: int | None = None) -> dict[tuple[str, str, int], float]:
    """(normalized_name, position, week) -> offensive snap share, from
    nflverse's snap_counts release (NFLStatsSource.get_snap_counts()).
    Cross-references against player_stats by normalized name — verified
    live on 2024 week 1: 301/303 skill-position players matched (the 2
    misses were suffix formatting, e.g. "Gabe Davis" vs "Gabriel Davis").
    """
    df = df[df["season"] == season]
    if through_week is not None:
        df = df[df["week"] < through_week]
    df = df[df["position"].isin(NFL_POSITION_MAP.keys())]
    out: dict[tuple[str, str, int], float] = {}
    for _, row in df.iterrows():
        position = NFL_POSITION_MAP.get(row["position"])
        if not position:
            continue
        out[(normalize_name(row["player"]), position, int(row["week"]))] = float(row.get("offense_pct") or 0.0)
    return out


def build_game_logs_by_player(
    df: pd.DataFrame, season: int, through_week: int | None = None, snap_df: pd.DataFrame | None = None,
) -> dict[tuple[str, str], list[dict]]:
    """Returns {(normalized_name, position): [game_stats, ...]} ordered
    oldest-first, restricted to `season` (and optionally up through a
    given week — used when backtesting a specific historical slate so we
    never leak future data into a projection).

    `snap_df` (nflverse's snap_counts release, optional) merges in each
    game's offensive snap share as a "snap_pct" key — a leading indicator
    of role change that moves before touches/targets do, and (unlike
    depth-chart rank, which only updates periodically) reflects what
    actually happened on the field that specific week. Omitted from a
    game's dict entirely when unavailable, rather than defaulted to 0,
    so downstream averaging doesn't mistake "no data" for "didn't play."
    """
    df = df[df["season"] == season]
    if through_week is not None:
        df = df[df["week"] < through_week]
    df = df[df["position"].isin(NFL_POSITION_MAP.keys())]
    df = df.sort_values(["week"])

    snap_lookup = build_snap_pct_lookup(snap_df, season, through_week) if snap_df is not None else {}

    logs: dict[tuple[str, str], list[dict]] = {}
    for _, row in df.iterrows():
        position = NFL_POSITION_MAP.get(row["position"])
        if not position:
            continue
        key = (normalize_name(row["player_display_name"]), position)
        stats = nflverse_row_to_game_stats(row)
        snap_pct = snap_lookup.get((key[0], key[1], int(row["week"])))
        if snap_pct is not None:
            stats["snap_pct"] = snap_pct
        logs.setdefault(key, []).append(stats)
    return logs


def build_fpts_allowed_by_team_position(df: pd.DataFrame, season: int, through_week: int | None = None) -> dict[tuple[str, str], float]:
    """Average DK-style PPR fantasy points a team's DEFENSE has allowed to
    each offensive position — the raw input to features/matchup.py's
    z-score computation.
    """
    df = df[df["season"] == season]
    if through_week is not None:
        df = df[df["week"] < through_week]
    df = df[df["position"].isin(NFL_POSITION_MAP.keys())].copy()
    df["position_group"] = df["position"].map(NFL_POSITION_MAP)
    df["opponent_team"] = df["opponent_team"].astype(str).map(normalize_team_abbreviation)

    grouped = df.groupby(["opponent_team", "position_group"])["fantasy_points_ppr"].mean()
    return {(team, pos): round(float(val), 2) for (team, pos), val in grouped.items()}
