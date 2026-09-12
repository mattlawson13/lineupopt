"""Proprietary ownership projection model — spec section 11.

This is a transparent, linear-in-features model (never a black box): each
player gets a small `feature_breakdown` dict alongside the final number so
the UI can show exactly which inputs pushed ownership up or down. When a
market ownership projection has been imported for the slate (from a
provider export), prefer that and skip this model entirely — see
`OwnershipProjection.source` in app/models/analytics.py.
"""
from __future__ import annotations

import dataclasses
import math
import statistics

from app.config.loader import get_dk_roster_rules, get_ownership_settings

INJURY_SEVERITY = {"healthy": 0.0, "questionable": 1.0, "doubtful": 2.0, "out": 3.0, "ir": 3.0}


@dataclasses.dataclass
class PlayerOwnershipInput:
    player_id: str
    position: str
    salary: int
    ensemble_projection: float
    implied_team_total: float
    spread: float  # negative = favored
    recent_avg_points: float | None
    injury_status: str = "healthy"
    is_chalk_narrative: bool = False  # e.g. "clear best value at position", set by slate builder heuristics
    popularity_prior: float = 0.0     # optional manual nudge, defaults to neutral


@dataclasses.dataclass
class OwnershipResult:
    player_id: str
    projected_ownership_pct: float
    feature_breakdown: dict


def _zscore(values: list[float], value: float) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values) or 1.0
    return (value - mean) / stdev


def project_ownership(
    players: list[PlayerOwnershipInput], contest_type: str = "classic"
) -> list[OwnershipResult]:
    cfg = get_ownership_settings()
    feat_coefs = cfg["features"]
    intercepts = cfg["intercept_by_position"]

    values_pct = [p.ensemble_projection / max(p.salary, 1) * 1000 for p in players]
    totals = [p.implied_team_total for p in players]
    spreads = [p.spread for p in players]
    recents = [p.recent_avg_points for p in players if p.recent_avg_points is not None]

    # Position-level projection percentile ranks (0-1), computed within
    # each position group so a top-5 WR and a top-2 TE are compared to
    # their own peer group rather than the whole slate.
    by_position: dict[str, list[PlayerOwnershipInput]] = {}
    for p in players:
        by_position.setdefault(p.position, []).append(p)
    rank_pct: dict[str, float] = {}
    for pos, group in by_position.items():
        sorted_group = sorted(group, key=lambda x: x.ensemble_projection)
        n = len(sorted_group)
        for i, p in enumerate(sorted_group):
            rank_pct[p.player_id] = (i + 1) / n if n > 0 else 0.5

    results: list[OwnershipResult] = []
    raw_scores: dict[str, float] = {}
    breakdowns: dict[str, dict] = {}

    for p in players:
        value_pct = p.ensemble_projection / max(p.salary, 1) * 1000
        features = {
            "value_zscore": _zscore(values_pct, value_pct),
            "projection_rank_pct": rank_pct.get(p.player_id, 0.5),
            "salary_tier": p.salary / 10000.0,
            "implied_team_total_zscore": _zscore(totals, p.implied_team_total),
            "favorite_zscore": -_zscore(spreads, p.spread),  # more negative spread (bigger favorite) -> positive feature
            "recent_performance_zscore": _zscore(recents, p.recent_avg_points) if p.recent_avg_points is not None else 0.0,
            "popularity_prior": p.popularity_prior,
            "injury_penalty": INJURY_SEVERITY.get(p.injury_status, 0.0),
            "chalk_narrative_bonus": 1.0 if p.is_chalk_narrative else 0.0,
        }
        score = intercepts.get(p.position, 0.1) + sum(feat_coefs[k] * v for k, v in features.items())
        raw_scores[p.player_id] = score
        breakdowns[p.player_id] = features

    # Softmax-normalize within each position group so total "rostership
    # share" for a position matches how many roster slots that position
    # fills per lineup (spec: "normalize to roster slots").
    roster_rules = get_dk_roster_rules("nfl", contest_type)
    slots_by_eligible_position: dict[str, float] = {}
    for slot, info in roster_rules["positions"].items():
        share = 1.0 / len(info["eligible"])
        for pos in info["eligible"]:
            slots_by_eligible_position[pos] = slots_by_eligible_position.get(pos, 0.0) + info["count"] * share

    for pos, group in by_position.items():
        target_total_pct = slots_by_eligible_position.get(pos, 1.0) * 100.0
        exp_scores = {p.player_id: math.exp(raw_scores[p.player_id]) for p in group}
        denom = sum(exp_scores.values()) or 1.0
        for p in group:
            raw_pct = exp_scores[p.player_id] / denom * target_total_pct
            clamped = min(max(raw_pct, cfg["min_ownership_pct"]), cfg["max_ownership_pct"])
            results.append(
                OwnershipResult(
                    player_id=p.player_id,
                    projected_ownership_pct=round(clamped, 2),
                    feature_breakdown=breakdowns[p.player_id],
                )
            )

    return results


def compute_leverage(projected_ownership_pct: float, optimal_ownership_pct: float) -> float:
    return round(optimal_ownership_pct - projected_ownership_pct, 2)


def compute_chalk_contrarian_scores(results: list[OwnershipResult]) -> dict[str, dict]:
    """Chalk score / contrarian score are simply the percentile rank of
    ownership within the slate (high ownership -> high chalk score, low
    ownership -> high contrarian score), each in [0, 100].
    """
    sorted_by_own = sorted(results, key=lambda r: r.projected_ownership_pct)
    n = len(sorted_by_own)
    out: dict[str, dict] = {}
    for i, r in enumerate(sorted_by_own):
        chalk = round((i + 1) / n * 100, 1) if n else 50.0
        out[r.player_id] = {"chalk_score": chalk, "contrarian_score": round(100 - chalk, 1)}
    return out
