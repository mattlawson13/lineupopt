"""Positional matchup strength, expressed as a z-score (positive =
favorable matchup for the offensive player / unfavorable for a defense
facing a good offense), computed from opponent's trailing fantasy-points-
allowed-by-position relative to league average — a transparent proxy for
DVOA-style matchup ratings that doesn't require a licensed data feed.
"""
from __future__ import annotations

import statistics


def compute_matchup_zscores(
    fpts_allowed_by_team_position: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    """`fpts_allowed_by_team_position`: {(team_abbrev, position): trailing
    avg DK fantasy points allowed to that position}. Returns a z-score per
    (team, position) relative to the league-wide mean/stdev for that
    position, so "the opponent's matchup rating" is always relative to the
    rest of the league this same week, not an arbitrary absolute number.
    """
    by_position: dict[str, list[float]] = {}
    for (_, position), value in fpts_allowed_by_team_position.items():
        by_position.setdefault(position, []).append(value)

    stats_by_position = {
        pos: (statistics.mean(vals), statistics.pstdev(vals) or 1.0)
        for pos, vals in by_position.items()
        if vals
    }

    zscores: dict[tuple[str, str], float] = {}
    for (team, position), value in fpts_allowed_by_team_position.items():
        mean, stdev = stats_by_position.get(position, (value, 1.0))
        zscores[(team, position)] = round((value - mean) / stdev, 3)
    return zscores
