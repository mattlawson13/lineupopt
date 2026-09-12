"""Derives game-environment features (implied team totals, win probability)
from a raw Vegas line — spread + total — per spec section 7.
"""
from __future__ import annotations

import dataclasses
import math


@dataclasses.dataclass
class GameEnvironment:
    home_implied_total: float
    away_implied_total: float
    home_spread: float  # negative = home favored
    total: float
    home_win_prob: float
    away_win_prob: float


def compute_game_environment(home_spread: float, total: float) -> GameEnvironment:
    """Standard sportsbook decomposition: implied_total = total/2 +/- spread/2.
    Win probability derived from the spread via a logistic approximation
    (13.5 points ≈ 1 std dev in the NFL is a widely used rule of thumb).
    """
    home_implied = total / 2 - home_spread / 2
    away_implied = total / 2 + home_spread / 2

    std_dev_points = 13.5
    z = -home_spread / std_dev_points
    home_win_prob = 1 / (1 + math.exp(-1.8 * z))
    away_win_prob = 1 - home_win_prob

    return GameEnvironment(
        home_implied_total=round(home_implied, 2),
        away_implied_total=round(away_implied, 2),
        home_spread=home_spread,
        total=total,
        home_win_prob=round(home_win_prob, 4),
        away_win_prob=round(away_win_prob, 4),
    )
