"""Player correlation engine — spec section 9.

Builds a full pairwise correlation matrix for a slate. For any pair with
enough historical paired-game samples, correlation is computed empirically
from actual fantasy-point co-movement and shrunk toward a configured prior;
otherwise the prior alone is used and the entry is flagged
`is_empirical=False` so nothing downstream (UI, AI layer, optimizer)
overstates its confidence. Conditional multipliers then adjust for this
specific slate's game environment (spec: "QB + WR becomes much stronger in
high-total games").
"""
from __future__ import annotations

import dataclasses

import numpy as np

from app.config.loader import get_correlation_settings


@dataclasses.dataclass
class CorrelationPlayer:
    player_id: str
    position: str
    team: str
    opponent: str


@dataclasses.dataclass
class CorrelationEntry:
    player_a_id: str
    player_b_id: str
    relationship_type: str
    correlation: float
    is_empirical: bool
    sample_games: int
    condition: dict


def classify_relationship(a: CorrelationPlayer, b: CorrelationPlayer) -> str | None:
    """Returns a relationship_type key matching correlation_settings.yaml's
    `priors`, or None if this pair has no modeled relationship (e.g. two
    unrelated players in different games).
    """
    same_team = a.team == b.team
    opponents = a.team == b.opponent and b.team == a.opponent
    if not same_team and not opponents:
        return None

    pa, pb = a.position, b.position
    pair = tuple(sorted([pa, pb]))

    if same_team:
        if pair == ("QB", "WR"):
            return "qb_own_wr"
        if pair == ("QB", "TE"):
            return "qb_own_te"
        if pair == ("QB", "RB"):
            return "qb_own_rb"
        if pair == ("DST", "RB"):
            return "rb_own_dst"
        if pair == ("DST", "K"):
            return "k_own_dst"
        if pair == ("TE", "WR"):
            return "te_own_wr"
        if pa == pb == "WR":
            return "wr_own_wr"
        return None

    # opponents
    if pair == ("QB", "QB"):
        return "qb_opp_qb"
    if pair == ("QB", "WR"):
        return "qb_opp_wr"
    if pair == ("QB", "TE"):
        return "qb_opp_te"
    if pair == ("DST", "RB"):
        return "rb_opp_dst"
    if pair == ("QB", "RB"):
        return "rb_opp_qb"
    if pair == ("DST", "QB"):
        return "dst_opp_qb"
    if pair == ("DST", "WR") or pair == ("DST", "TE") or pair == ("DST", "RB"):
        return "dst_opp_offense"
    return None


def compute_empirical_correlation(series_a: list[float], series_b: list[float]) -> tuple[float, int]:
    n = min(len(series_a), len(series_b))
    if n < 3:
        return 0.0, n
    a = np.array(series_a[:n])
    b = np.array(series_b[:n])
    if a.std() == 0 or b.std() == 0:
        return 0.0, n
    corr = float(np.corrcoef(a, b)[0, 1])
    return round(corr, 4), n


def _shrink(prior: float, empirical: float, sample_games: int, cfg: dict) -> float:
    min_n = cfg["min_sample_games"]
    full_n = cfg["shrinkage_full_empirical_at_games"]
    if sample_games < min_n:
        return prior
    if sample_games >= full_n:
        return empirical
    # linear ramp from `shrinkage_weight_at_min_sample` (weight on prior) at
    # min_n down to 0 at full_n
    progress = (sample_games - min_n) / max(full_n - min_n, 1)
    prior_weight = cfg["shrinkage_weight_at_min_sample"] * (1 - progress)
    return prior_weight * prior + (1 - prior_weight) * empirical


def _conditional_multiplier(relationship_type: str, game_total: float | None, spread: float | None, cfg: dict) -> float:
    stack_sensitive = {"qb_own_wr", "qb_own_te", "qb_opp_wr", "qb_opp_te", "qb_opp_qb"}
    if relationship_type not in stack_sensitive:
        return 1.0
    mult = 1.0
    cond = cfg["conditional"]
    if game_total is not None:
        if game_total >= cond["high_total_threshold"]:
            mult *= cond["high_total_multiplier"]
        elif game_total <= cond["low_total_threshold"]:
            mult *= cond["low_total_multiplier"]
    if spread is not None and abs(spread) <= cond["close_spread_threshold"]:
        mult *= cond["close_spread_multiplier"]
    return mult


def build_correlation_matrix(
    players: list[CorrelationPlayer],
    historical_series: dict[tuple[str, str], tuple[list[float], list[float]]] | None = None,
    game_context: dict[str, dict] | None = None,  # team -> {"total": x, "spread": y}
) -> list[CorrelationEntry]:
    cfg = get_correlation_settings()
    historical_series = historical_series or {}
    game_context = game_context or {}

    entries: list[CorrelationEntry] = []
    for i, a in enumerate(players):
        for b in players[i + 1 :]:
            rel = classify_relationship(a, b)
            if rel is None:
                continue

            prior = cfg["priors"].get(rel, 0.0)
            key = tuple(sorted([a.player_id, b.player_id]))
            series = historical_series.get(key)
            if series:
                empirical, n = compute_empirical_correlation(*series)
                corr = _shrink(prior, empirical, n, cfg)
                is_empirical = n >= cfg["min_sample_games"]
            else:
                corr, n, is_empirical = prior, 0, False

            ctx = game_context.get(a.team, {})
            mult = _conditional_multiplier(rel, ctx.get("total"), ctx.get("spread"), cfg)
            corr = round(max(min(corr * mult, 1.0), -1.0), 4)

            entries.append(
                CorrelationEntry(
                    player_a_id=a.player_id,
                    player_b_id=b.player_id,
                    relationship_type=rel,
                    correlation=corr,
                    is_empirical=is_empirical,
                    sample_games=n,
                    condition={"game_total": ctx.get("total"), "spread": ctx.get("spread"), "multiplier": mult},
                )
            )
    return entries
