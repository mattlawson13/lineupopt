"""Builds recency-weighted, shrinkage-adjusted usage-rate snapshots from a
player's recent game logs (spec section 7: "weight recent information
heavily ... but avoid overreacting to tiny samples").

Input is a list of per-game stat dicts, most recent last, each already
converted to the same rate-stat keys the projection models expect (e.g.
"rush_attempts_pg" doesn't make sense per-game — instead we store raw
per-game totals and derived rate stats and average them here).
"""
from __future__ import annotations

from app.config.loader import get_projection_weights

# Position-specific rate/count fields we track. "_pg" fields are summed and
# averaged directly; "_rate"/"pct" fields are computed as ratios of their
# numerator/denominator sums (never averaged-of-averages, which biases
# small samples).
RATE_FIELD_SOURCES = {
    "completion_pct": ("pass_completions", "pass_attempts"),
    "pass_yards_per_attempt": ("pass_yards", "pass_attempts"),
    "pass_td_rate": ("pass_td", "pass_attempts"),
    "int_rate": ("interceptions", "pass_attempts"),
    "rush_yards_per_carry": ("rush_yards", "rush_attempts"),
    "rush_td_rate": ("rush_td", "rush_attempts"),
    "catch_rate": ("receptions", "targets"),
    "rec_yards_per_reception": ("rec_yards", "receptions"),
    "rec_td_rate": ("rec_td", "receptions"),
    "fg_make_rate": ("fg_made", "fg_attempts"),
}

COUNT_FIELDS = [
    "pass_attempts", "rush_attempts", "targets", "fg_attempts", "xp_attempts",
    "fumbles_lost", "sacks", "interceptions", "fumble_recoveries",
    # DST-only (features/espn_adapter.py's build_dst_game_logs_by_team) —
    # nflverse never covered team defense, so these were unused/dead until
    # that adapter started producing real per-game DST rows.
    "def_td", "safety", "blocked_kick", "return_td", "points_allowed",
]


def _weighted_avg(games: list[dict], field: str, weights: list[float]) -> float:
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    return sum(g.get(field, 0.0) * w for g, w in zip(games, weights)) / total_w


def _recency_weights(n_games: int, cfg: dict) -> list[float]:
    """Most recent game gets last_1_game_weight, next 2 share
    last_3_game_weight, everything older shares season_weight — applied as
    per-game weights, oldest first to match `games` ordering.
    """
    if n_games == 0:
        return []
    weights = [cfg["season_weight"] / max(n_games - 3, 1)] * max(n_games - 3, 0)
    if n_games >= 3:
        weights += [cfg["last_3_game_weight"] / 3] * min(3, n_games - 1 if n_games > 1 else 0)
    elif n_games >= 2:
        weights += [cfg["last_3_game_weight"] / 2] * (n_games - 1)
    if n_games >= 1:
        weights += [cfg["last_1_game_weight"]]
    # Ensure list length matches n_games (pad/trim defensively)
    weights = weights[-n_games:]
    while len(weights) < n_games:
        weights.insert(0, cfg["season_weight"] / max(n_games, 1))
    return weights


def _shrink_rate(numerator: float, denominator: float, prior_rate: float, prior_games_weight: float) -> float:
    """Bayesian-ish shrinkage: blend the observed rate toward a positional
    prior, with weight proportional to sample size vs `prior_games_weight`
    (the config's `usage_shrinkage.prior_games`).
    """
    if denominator + prior_games_weight == 0:
        return prior_rate
    return (numerator + prior_rate * prior_games_weight) / (denominator + prior_games_weight)


def compute_usage_snapshot(games: list[dict], position_priors: dict | None = None) -> dict:
    """`games`: list of per-game stat dicts, OLDEST FIRST. Returns a single
    rate-stat dict suitable as `season_usage` (all games) — call again with
    just the last 1-3 games for `recent_usage` if you want an explicit
    short-window snapshot, or rely on the recency weighting here directly.
    """
    priors = position_priors or {}
    weights_cfg = get_projection_weights()["usage_recency"]
    shrink_cfg = get_projection_weights()["usage_shrinkage"]
    prior_games_weight = shrink_cfg["prior_games"]

    if not games:
        return dict(priors)

    weights = _recency_weights(len(games), weights_cfg)

    out: dict = {}
    for field in COUNT_FIELDS:
        pg_key = f"{field}_pg" if not field.endswith("_pg") else field
        out[pg_key] = round(_weighted_avg(games, field, weights), 3)

    for rate_name, (num_field, den_field) in RATE_FIELD_SOURCES.items():
        num_sum = sum(g.get(num_field, 0.0) for g in games)
        den_sum = sum(g.get(den_field, 0.0) for g in games)
        prior_rate = priors.get(rate_name, 0.5)
        out[rate_name] = round(_shrink_rate(num_sum, den_sum, prior_rate, prior_games_weight), 4)

    # snap_pct is already a per-game ratio (nflverse's snap_counts release
    # — see build_game_logs_by_player's snap_df param), not a num/den pair
    # like the RATE_FIELD_SOURCES above, so it's weighted-averaged
    # directly. Not every game log has it (only merged in when the two
    # nflverse files cross-referenced successfully — see
    # build_snap_pct_lookup), so this only averages over games that
    # actually have a value, re-weighted among just those, rather than
    # defaulting a missing game to 0 and wrongly implying "played no
    # snaps" for what's really just a data gap.
    snap_games = [(g, w) for g, w in zip(games, weights) if "snap_pct" in g]
    if snap_games:
        snap_weights = [w for _, w in snap_games]
        out["snap_pct"] = round(_weighted_avg([g for g, _ in snap_games], "snap_pct", snap_weights), 4)

    return out
