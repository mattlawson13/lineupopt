"""Sport abstraction — spec section 25/27.

Every sport-specific piece of the pipeline (scoring, roster rules,
projection model, correlation relationships, game-script simulation) is
reached through a `Sport` implementation rather than called directly, so
the ingestion/optimization/simulation layers never hardcode NFL
assumptions. Adding a new sport means implementing this interface, not
touching `ingestion/slate_builder.py` or `optimization/optimizer.py`.

NFL (`sports/nfl/sport.py`) is the full, production implementation.
Soccer (`sports/soccer/sport.py`) is currently a scaffold: it wires up
real config (scoring/roster rules) but raises `NotImplementedError` on the
modeling methods rather than pretending to project soccer players with
NFL-shaped math (spec section 25: "Do not force NFL assumptions onto
Soccer").
"""
from __future__ import annotations

import abc

from app.projections.nfl.common import ComponentProjection, PlayerProjectionContext


class Sport(abc.ABC):
    key: str  # "nfl" | "soccer"
    positions: list[str]

    @abc.abstractmethod
    def scoring_config_name(self) -> str: ...

    @abc.abstractmethod
    def roster_config_name(self, contest_type: str) -> str: ...

    @abc.abstractmethod
    def project_player(self, ctx: PlayerProjectionContext) -> ComponentProjection:
        """Runs the sport's component-based projection model for one player."""

    @abc.abstractmethod
    def classify_correlation_relationship(self, position_a: str, position_b: str, same_team: bool) -> str | None:
        """Returns a relationship_type key (matching this sport's
        correlation prior config) for a pair of positions, or None if this
        sport/pair has no modeled relationship.
        """

    @abc.abstractmethod
    def game_script_names(self) -> list[str]: ...
