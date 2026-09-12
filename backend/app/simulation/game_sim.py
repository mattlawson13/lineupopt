"""Game-script simulation — spec section 8.

We do not project every player independently. Each simulated game draws a
game-script scenario (close/blowout/shootout/etc.), conditioned on that
game's own Vegas spread and total, and every player in that game has their
simulated mean nudged by a script- and team-role-specific multiplier
(e.g. the trailing team's WRs get more volume in a blowout).
"""
from __future__ import annotations

import dataclasses

import numpy as np

from app.config.loader import get_simulation_settings
from app.models.enums import GameScript

# Position multipliers for the four "symmetric" scripts (neither team is
# specifically leading/trailing).
SYMMETRIC_SCRIPT_MULTIPLIERS: dict[str, dict[str, float]] = {
    GameScript.CLOSE_HIGH_SCORING: {"QB": 1.08, "RB": 0.95, "WR": 1.10, "TE": 1.05, "K": 1.05, "DST": 0.85},
    GameScript.LOW_SCORING: {"QB": 0.85, "RB": 1.05, "WR": 0.85, "TE": 0.85, "K": 0.85, "DST": 1.15},
    GameScript.COMPETITIVE_SHOOTOUT: {"QB": 1.15, "RB": 0.90, "WR": 1.15, "TE": 1.08, "K": 1.05, "DST": 0.75},
    GameScript.DEFENSIVE_GAME: {"QB": 0.80, "RB": 1.00, "WR": 0.80, "TE": 0.80, "K": 0.80, "DST": 1.25},
}

# For blowout scripts, multipliers depend on whether this player's team is
# leading or trailing (spec section 8's worked example).
BLOWOUT_ROLE_MULTIPLIERS: dict[str, dict[str, float]] = {
    "leading": {"QB": 0.92, "RB": 1.20, "WR": 0.90, "TE": 0.92, "K": 1.05, "DST": 1.10},
    "trailing": {"QB": 1.18, "RB": 0.75, "WR": 1.15, "TE": 1.05, "K": 0.85, "DST": 0.75},
}


@dataclasses.dataclass
class GameSimInput:
    game_id: str
    home_team: str
    away_team: str
    home_spread: float  # negative = home favored
    total: float


def scenario_probabilities(spread: float, total: float) -> dict[str, float]:
    """Reweights the configured base scenario probabilities toward
    blowouts as |spread| grows, and toward high-scoring scripts as total
    rises (and vice versa) — see config/simulation_settings.yaml.
    """
    cfg = get_simulation_settings()["game_scripts"]
    base = {row["name"]: row["base_probability"] for row in cfg}

    abs_spread = abs(spread)
    blowout_boost = min(abs_spread / 14.0, 1.5)  # ramps up to 1.5x by a 21pt spread
    close_boost = max(1.4 - abs_spread / 10.0, 0.4)

    total_z = (total - 44.0) / 6.0  # ~44 is a typical league-average NFL total
    high_scoring_boost = max(1.0 + total_z * 0.35, 0.3)
    low_scoring_boost = max(1.0 - total_z * 0.35, 0.3)

    weights = {
        "home_blowout": base["home_blowout"] * blowout_boost * (1.3 if spread < 0 else 0.7),
        "away_blowout": base["away_blowout"] * blowout_boost * (1.3 if spread > 0 else 0.7),
        "close_high_scoring": base["close_high_scoring"] * close_boost * high_scoring_boost,
        "competitive_shootout": base["competitive_shootout"] * close_boost * high_scoring_boost,
        "low_scoring": base["low_scoring"] * low_scoring_boost,
        "defensive_game": base["defensive_game"] * low_scoring_boost * (0.6 + min(abs_spread / 20, 0.8)),
    }
    total_w = sum(weights.values())
    return {k: v / total_w for k, v in weights.items()}


def draw_game_scripts(game: GameSimInput, num_sims: int, rng: np.random.Generator) -> np.ndarray:
    """Returns an array of shape (num_sims,) of script name strings."""
    probs = scenario_probabilities(game.home_spread, game.total)
    names = list(probs.keys())
    p = np.array([probs[n] for n in names])
    idx = rng.choice(len(names), size=num_sims, p=p)
    return np.array(names)[idx]


def multiplier_for(script: str, position: str, team_role: str | None) -> float:
    """`team_role`: "home"/"away", used only to resolve leading/trailing on
    blowout scripts; ignored for symmetric scripts.
    """
    if script == GameScript.HOME_BLOWOUT.value:
        role = "leading" if team_role == "home" else "trailing"
        return BLOWOUT_ROLE_MULTIPLIERS[role].get(position, 1.0)
    if script == GameScript.AWAY_BLOWOUT.value:
        role = "leading" if team_role == "away" else "trailing"
        return BLOWOUT_ROLE_MULTIPLIERS[role].get(position, 1.0)
    return SYMMETRIC_SCRIPT_MULTIPLIERS.get(script, {}).get(position, 1.0)
