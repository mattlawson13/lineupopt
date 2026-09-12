"""Combines model/market/manually-imported projections into a single
weighted-consensus "ensemble" projection — spec section 3: never ship a
single source's number, always a transparent, reweightable blend.
"""
from __future__ import annotations

import dataclasses

from app.config.loader import get_projection_weights, get_simulation_settings

# Heuristic share of a team's implied total that typically accrues to each
# position as DK fantasy points — used to derive `market_projection`
# directly from the betting line without needing a licensed market-
# projections feed. These are starting priors; backtesting (spec section
# 24) is expected to recalibrate them over time.
MARKET_SHARE_OF_IMPLIED_TOTAL = {
    "QB": 0.95, "RB": 0.42, "WR": 0.34, "TE": 0.22, "K": 0.35,
}


def market_projection_from_vegas(position: str, implied_team_total: float, opponent_implied_total: float) -> float:
    if position == "DST":
        return round(max((24.0 - opponent_implied_total) * 0.45 + 5.5, -4.0), 2)
    share = MARKET_SHARE_OF_IMPLIED_TOTAL.get(position, 0.3)
    return round(implied_team_total * share, 2)


@dataclasses.dataclass
class SourceProjections:
    model_projection: float | None = None
    model_std_dev: float | None = None
    market_projection: float | None = None
    source_projection_1: float | None = None
    source_projection_2: float | None = None
    source_projection_3: float | None = None
    source_confidences: dict = dataclasses.field(default_factory=dict)  # {field_name: confidence in [0,1]}


@dataclasses.dataclass
class EnsembleResult:
    ensemble_projection: float
    floor: float
    median: float
    ceiling: float
    std_dev: float
    weights_used: dict


def compute_ensemble(sources: SourceProjections, position: str) -> EnsembleResult:
    weights_cfg = get_projection_weights()["ensemble_weights"]
    default_conf = get_projection_weights()["default_source_confidence"]

    present = {
        field: value
        for field, value in dataclasses.asdict(sources).items()
        if field in weights_cfg and value is not None
    }

    if not present:
        raise ValueError("No projection sources available to build an ensemble for this player")

    weighted_sum = 0.0
    weight_total = 0.0
    weights_used = {}
    for field, value in present.items():
        conf = sources.source_confidences.get(field, default_conf)
        w = weights_cfg[field] * conf
        weighted_sum += value * w
        weight_total += w
        weights_used[field] = round(w, 4)

    ensemble_projection = round(weighted_sum / weight_total, 2) if weight_total > 0 else 0.0

    # Uncertainty: prefer the model's own component-derived std_dev when
    # present (it reflects this specific player/game), otherwise fall back
    # to the position's configured coefficient of variation.
    if sources.model_std_dev is not None:
        std_dev = sources.model_std_dev
    else:
        cv = get_simulation_settings()["player_variance"].get(position, {}).get("cv", 0.5)
        std_dev = round(max(ensemble_projection, 0.0) * cv, 2)

    # Spread across sources adds extra uncertainty beyond any single
    # source's own variance — widen std_dev when sources disagree.
    values = list(present.values())
    if len(values) > 1:
        spread = (max(values) - min(values)) / 2
        std_dev = round((std_dev**2 + (spread * 0.5) ** 2) ** 0.5, 2)

    floor = round(max(ensemble_projection - std_dev, 0.0), 2)
    ceiling = round(ensemble_projection + std_dev * 1.4, 2)

    return EnsembleResult(
        ensemble_projection=ensemble_projection,
        floor=floor,
        median=ensemble_projection,
        ceiling=ceiling,
        std_dev=std_dev,
        weights_used=weights_used,
    )
