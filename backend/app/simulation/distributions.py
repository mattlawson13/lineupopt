"""Marginal distribution helpers: gamma-distributed fantasy points
parameterized by (mean, coefficient of variation), and percentile/
probability summarization of a simulated sample array.
"""
from __future__ import annotations

import dataclasses

import numpy as np
from scipy import stats

from app.config.loader import get_simulation_settings


def gamma_params(mean: float | np.ndarray, cv: float) -> tuple[float, float | np.ndarray]:
    """Returns (shape k, scale theta) for a gamma distribution with the
    given mean and coefficient of variation (stdev / mean). `mean` may be
    a scalar or a per-simulation array (e.g. after a game-script
    multiplier has been applied) — `cv` is always a scalar per player.
    """
    mean = np.clip(mean, 0.01, None) if isinstance(mean, np.ndarray) else max(mean, 0.01)
    cv = max(cv, 0.05)
    k = 1.0 / (cv**2)
    theta = mean / k
    return k, theta


def gamma_ppf_from_normal(std_normal_draws: np.ndarray, mean: float | np.ndarray, cv: float) -> np.ndarray:
    """Transforms standard-normal draws (already correlated via a copula)
    into gamma-distributed fantasy-point draws with the given mean/cv,
    preserving the correlation structure (Gaussian copula method). `mean`
    may vary per simulation (broadcasts against `std_normal_draws`).
    """
    k, theta = gamma_params(mean, cv)
    u = stats.norm.cdf(std_normal_draws)
    u = np.clip(u, 1e-6, 1 - 1e-6)
    return stats.gamma.ppf(u, a=k, scale=theta)


@dataclasses.dataclass
class SimulationSummary:
    mean: float
    median: float
    std_dev: float
    floor: float
    ceiling: float
    percentiles: dict[str, float]
    prob_3x_salary: float
    prob_4x_salary: float
    prob_5x_salary: float
    prob_6x_salary: float
    prob_top1pct: float
    prob_top5pct: float


def summarize_player_draws(
    draws: np.ndarray,
    salary: int,
    top1pct_flags: np.ndarray | None = None,
    top5pct_flags: np.ndarray | None = None,
) -> SimulationSummary:
    cfg = get_simulation_settings()
    pct_targets = cfg["percentiles"]
    pcts = {str(p): round(float(np.percentile(draws, p)), 2) for p in pct_targets}

    salary_k = salary / 1000.0
    mults = cfg["salary_multiplier_targets"]

    return SimulationSummary(
        mean=round(float(draws.mean()), 2),
        median=round(float(np.median(draws)), 2),
        std_dev=round(float(draws.std()), 2),
        floor=pcts.get("10", round(float(np.percentile(draws, 10)), 2)),
        ceiling=pcts.get("90", round(float(np.percentile(draws, 90)), 2)),
        percentiles=pcts,
        prob_3x_salary=round(float((draws >= mults[0] * salary_k).mean()), 4) if salary_k else 0.0,
        prob_4x_salary=round(float((draws >= mults[1] * salary_k).mean()), 4) if salary_k else 0.0,
        prob_5x_salary=round(float((draws >= mults[2] * salary_k).mean()), 4) if salary_k else 0.0,
        prob_6x_salary=round(float((draws >= mults[3] * salary_k).mean()), 4) if salary_k else 0.0,
        prob_top1pct=round(float(top1pct_flags.mean()), 4) if top1pct_flags is not None else 0.0,
        prob_top5pct=round(float(top5pct_flags.mean()), 4) if top5pct_flags is not None else 0.0,
    )
