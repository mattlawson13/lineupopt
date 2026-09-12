from __future__ import annotations

from app.sports.base import Sport
from app.sports.nfl.sport import NFLSport
from app.sports.soccer.sport import SoccerSport

_REGISTRY: dict[str, Sport] = {
    "nfl": NFLSport(),
    "soccer": SoccerSport(),
}


def get_sport(key: str) -> Sport:
    if key not in _REGISTRY:
        raise ValueError(f"Unknown sport: {key!r}. Registered: {list(_REGISTRY)}")
    return _REGISTRY[key]
