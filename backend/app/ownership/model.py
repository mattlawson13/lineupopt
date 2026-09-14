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
from app.optimization.eligibility import UNPLAYABLE_PROJECTION_FLOOR

INJURY_SEVERITY = {"healthy": 0.0, "questionable": 1.0, "doubtful": 2.0, "out": 3.0, "ir": 3.0}

# Floor used when computing points-per-$1000-salary for the value_zscore
# feature (NOT for the "value" column shown in the player pool — that's a
# separate, standard DFS display metric and is left alone). Confirmed
# live: without a floor, a $200 Showdown FLEX practice-squad player
# (depth-chart-discounted to a 2-3 point projection) produces a
# points-per-dollar ratio 5-10x every real player's, because the ratio
# only cares how small the denominator is, not whether the player will
# ever actually be rostered. That single feature — the model's largest
# coefficient (0.30) — was enough to project a completely unranked bench
# player above several legitimate rostered backups (16%+ ownership).
# Evaluating value as if every player cost at least $3000 (the salary
# range where real bench-vs-starter distinctions actually live) keeps the
# feature meaningful for genuine value plays without this floor-price
# artifact.
_MIN_SALARY_FOR_VALUE = 3000


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


_ZSCORE_CLIP = 2.5  # see _zscore() docstring


def _zscore(values: list[float], value: float) -> float:
    """Clipped to +/-2.5 std devs as general defensive practice against
    any single feature dominating this linear model's raw score — the
    main fix for the specific value_zscore-at-the-salary-floor issue this
    was written alongside is `_MIN_SALARY_FOR_VALUE` above, not this clip
    (2.5 sigma alone didn't catch that case; see that constant's comment).
    """
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values) or 1.0
    z = (value - mean) / stdev
    return max(-_ZSCORE_CLIP, min(_ZSCORE_CLIP, z))


def project_ownership(
    players: list[PlayerOwnershipInput], contest_type: str = "classic"
) -> list[OwnershipResult]:
    cfg = get_ownership_settings()
    feat_coefs = cfg["features"]
    intercepts = cfg["intercept_by_position"]

    # Players with a near-zero real ensemble projection (inactive,
    # 3rd/4th-string emergency depth, etc.) are hard-clamped to the
    # ownership floor rather than competing in the softmax below. The
    # z-score features that drive the softmax are clipped at +/-2.5 sigma
    # (see _zscore) as general defensive practice — but that clip can't
    # distinguish "genuinely cannot play" from "just below average," so a
    # truly dead player can still retain a non-trivial softmax share,
    # especially in a thin position group. Confirmed live: a real Showdown
    # slate's backup QBs (0.02-0.32 projected points) still came out at
    # 13-20% projected ownership after fixing the position-target bug
    # below, purely from surviving in the softmax competition at all. The
    # same shared floor (optimization/eligibility.py) gates optimizer
    # eligibility everywhere a player pool gets built, since a player who
    # shouldn't be draftable shouldn't have real projected ownership either.
    viable = [p for p in players if p.ensemble_projection >= UNPLAYABLE_PROJECTION_FLOOR]
    unplayable = [p for p in players if p.ensemble_projection < UNPLAYABLE_PROJECTION_FLOOR]
    floor_results = [
        OwnershipResult(player_id=p.player_id, projected_ownership_pct=cfg["min_ownership_pct"], feature_breakdown={})
        for p in unplayable
    ]
    if not viable:
        return floor_results
    players = viable

    values_pct = [p.ensemble_projection / max(p.salary, _MIN_SALARY_FOR_VALUE) * 1000 for p in players]
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
        value_pct = p.ensemble_projection / max(p.salary, _MIN_SALARY_FOR_VALUE) * 1000
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

    # Softmax-normalize so total "rostership share" matches how many
    # roster slots exist to fill (spec: "normalize to roster slots").
    #
    # DK Showdown's CPT+FLEX slots are ALL eligible for every position
    # (see dk_roster_rules_nfl.yaml) — every slot draws from the exact
    # same shared pool. When every slot's eligible set is identical, doing
    # a PER-POSITION-independent softmax (as below) wrongly gives each
    # position its own fully independent 100% target, as if positions
    # don't compete for the same slots. Confirmed live on a real Showdown
    # build: with only 6 QBs on the slate and QB independently "entitled"
    # to a full 100% ownership share, backup QBs projected for 0.02-0.32
    # fantasy points came out at 8-16% projected ownership — higher than
    # some legitimately-played skill players — because there were too few
    # real QBs to absorb that inflated target. Positions genuinely DO
    # compete for the same slots in this format, so a single slate-wide
    # softmax (target = total roster slots x 100%) is used instead — a
    # bad player now competes directly against every other player on the
    # slate for ownership share, not just against other bad players at
    # the same position.
    roster_rules = get_dk_roster_rules("nfl", contest_type)
    eligible_sets = {frozenset(info["eligible"]) for info in roster_rules["positions"].values()}
    shares_all_slots = len(eligible_sets) == 1 and len(next(iter(eligible_sets))) > 1

    if shares_all_slots:
        total_slots = sum(info["count"] for info in roster_rules["positions"].values())
        target_total_pct = total_slots * 100.0
        exp_scores = {p.player_id: math.exp(raw_scores[p.player_id]) for p in players}
        denom = sum(exp_scores.values()) or 1.0
        for p in players:
            raw_pct = exp_scores[p.player_id] / denom * target_total_pct
            clamped = min(max(raw_pct, cfg["min_ownership_pct"]), cfg["max_ownership_pct"])
            results.append(
                OwnershipResult(
                    player_id=p.player_id,
                    projected_ownership_pct=round(clamped, 2),
                    feature_breakdown=breakdowns[p.player_id],
                )
            )
        return results + floor_results

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

    return results + floor_results


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
