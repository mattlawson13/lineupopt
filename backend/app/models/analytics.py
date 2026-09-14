"""Ownership, correlation, and simulation output models."""
from __future__ import annotations

import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPk


class OwnershipProjection(Base, UUIDPk, TimestampMixin):
    __tablename__ = "ownership_projections"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)

    projected_ownership_pct: Mapped[float] = mapped_column(Float)
    optimal_ownership_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # % of simulated-optimal lineups featuring this player
    leverage: Mapped[float | None] = mapped_column(Float, nullable=True)  # optimal% - projected_ownership%
    chalk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    contrarian_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="proprietary_model")  # proprietary_model | market_import
    feature_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)


class Correlation(Base, UUIDPk, TimestampMixin):
    __tablename__ = "correlations"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    player_a_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    player_b_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(32))  # e.g. qb_own_wr, rb_opp_dst
    correlation: Mapped[float] = mapped_column(Float)
    is_empirical: Mapped[bool] = mapped_column(default=False)
    sample_games: Mapped[int] = mapped_column(Integer, default=0)
    condition: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"high_total": true}


class SimulationRun(Base, UUIDPk, TimestampMixin):
    __tablename__ = "simulations"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    model_version_id: Mapped[str | None] = mapped_column(ForeignKey("model_versions.id"), nullable=True)
    num_simulations: Mapped[int] = mapped_column(Integer)
    random_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|failed
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class PlayerSimulationResult(Base, UUIDPk, TimestampMixin):
    """Aggregated distribution stats per player for a SimulationRun. Raw
    per-draw arrays are not persisted (too large); they're held in memory /
    a parquet cache during the run and summarized here — see
    simulation/monte_carlo.py.
    """

    __tablename__ = "player_simulation_results"

    simulation_run_id: Mapped[str] = mapped_column(ForeignKey("simulations.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)

    mean: Mapped[float] = mapped_column(Float)
    median: Mapped[float] = mapped_column(Float)
    std_dev: Mapped[float] = mapped_column(Float)
    floor: Mapped[float] = mapped_column(Float)
    ceiling: Mapped[float] = mapped_column(Float)
    percentiles: Mapped[dict] = mapped_column(JSON, default=dict)  # {"10": x, "25": x, "50": x, "75": x, "90": x, "95": x, "99": x}
    prob_3x_salary: Mapped[float] = mapped_column(Float, default=0.0)
    prob_4x_salary: Mapped[float] = mapped_column(Float, default=0.0)
    prob_5x_salary: Mapped[float] = mapped_column(Float, default=0.0)
    prob_6x_salary: Mapped[float] = mapped_column(Float, default=0.0)
    prob_top1pct: Mapped[float] = mapped_column(Float, default=0.0)
    prob_top5pct: Mapped[float] = mapped_column(Float, default=0.0)


class PlayerActualResult(Base, UUIDPk, TimestampMixin):
    """Real DK fantasy points a player actually scored on a slate, pulled
    from nflverse's box scores once the games are final — spec-adjacent to
    backtesting/engine.py, but scoped to one specific already-built slate
    rather than an arbitrary historical season/week range, so it can be
    directly diffed against that slate's own Projection/EnsembleProjection
    rows (see ingestion/resolution.py).
    """

    __tablename__ = "player_actual_results"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)

    actual_dk_points: Mapped[float] = mapped_column(Float)
    stat_line: Mapped[dict] = mapped_column(JSON, default=dict)
    projected_dk_points: Mapped[float | None] = mapped_column(Float, nullable=True)  # our EnsembleProjection.ensemble_projection at resolution time, for convenience
    error: Mapped[float | None] = mapped_column(Float, nullable=True)  # projected - actual


class SlateResolution(Base, UUIDPk, TimestampMixin):
    """One resolution pass for a slate: how our generated lineups actually
    would have scored, versus the best lineup obtainable in hindsight
    (same optimizer, run against real results instead of projections).
    The retro-optimal lineup itself is stored as an ordinary Lineup row
    (via its own OptimizationRun with objective="retro_optimal"), linked
    here by id, so it reuses every existing lineup-serialization path.
    """

    __tablename__ = "slate_resolutions"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    optimization_run_id: Mapped[str] = mapped_column(ForeignKey("optimization_runs.id"), index=True)  # the run being graded
    retro_optimal_lineup_id: Mapped[str | None] = mapped_column(ForeignKey("lineups.id"), nullable=True)

    our_best_actual_points: Mapped[float] = mapped_column(Float)  # our #1-ranked lineup's real score
    retro_optimal_points: Mapped[float] = mapped_column(Float)
    players_resolved: Mapped[int] = mapped_column(Integer, default=0)
    players_unmatched: Mapped[int] = mapped_column(Integer, default=0)
    mae: Mapped[float] = mapped_column(Float, default=0.0)  # mean absolute projection error across resolved players
    bias: Mapped[float] = mapped_column(Float, default=0.0)  # mean (projected - actual); positive = model ran hot
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # structured diff: missed players, biggest errors, etc. — see resolution.py
