"""Import every model module so `Base.metadata` sees the full schema when
create_all()/Alembic autogenerate run.
"""
from app.models.core import Game, Player, Team  # noqa: F401
from app.models.slate import Contest, DraftKingsPlayer, DraftKingsSalary, Slate  # noqa: F401
from app.models.context_data import (  # noqa: F401
    BettingLine,
    PlayerInjury,
    PlayerNews,
    PlayerSnapshot,
    PlayerStat,
    Weather,
)
from app.models.projections import EnsembleProjection, Projection, ProjectionSource  # noqa: F401
from app.models.analytics import (  # noqa: F401
    Correlation,
    OwnershipProjection,
    PlayerSimulationResult,
    SimulationRun,
)
from app.models.lineup import Lineup, LineupPlayer, ModelVersion, OptimizationRun  # noqa: F401
from app.models.backtest import BacktestResult  # noqa: F401

__all__ = [
    "Game",
    "Player",
    "Team",
    "Contest",
    "DraftKingsPlayer",
    "DraftKingsSalary",
    "Slate",
    "BettingLine",
    "PlayerInjury",
    "PlayerNews",
    "PlayerSnapshot",
    "PlayerStat",
    "Weather",
    "EnsembleProjection",
    "Projection",
    "ProjectionSource",
    "Correlation",
    "OwnershipProjection",
    "PlayerSimulationResult",
    "SimulationRun",
    "Lineup",
    "LineupPlayer",
    "ModelVersion",
    "OptimizationRun",
    "BacktestResult",
]
