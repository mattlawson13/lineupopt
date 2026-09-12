"""Generated lineups, optimization runs, and model versioning."""
from __future__ import annotations

import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPk


class ModelVersion(Base, UUIDPk, TimestampMixin):
    """Every projection/ownership/simulation run is tagged with a model
    version so results are reproducible and comparable across backtests.
    """

    __tablename__ = "model_versions"

    sport: Mapped[str] = mapped_column(String(16), index=True)
    version_tag: Mapped[str] = mapped_column(String(32))  # e.g. "nfl-proj-2026.09.1"
    component: Mapped[str] = mapped_column(String(32))  # projection | ownership | correlation | simulation
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # frozen copy of the yaml config used
    is_active: Mapped[bool] = mapped_column(default=True)


class OptimizationRun(Base, UUIDPk, TimestampMixin):
    __tablename__ = "optimization_runs"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    simulation_run_id: Mapped[str | None] = mapped_column(ForeignKey("simulations.id"), nullable=True)
    objective: Mapped[str] = mapped_column(String(32))  # OptimizationObjective value
    num_lineups_requested: Mapped[int] = mapped_column(Integer)
    num_lineups_generated: Mapped[int] = mapped_column(Integer, default=0)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # exposure caps, stacking rules, locks/excludes
    started_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")

    lineups: Mapped[list["Lineup"]] = relationship(back_populates="optimization_run")


class Lineup(Base, UUIDPk, TimestampMixin):
    __tablename__ = "lineups"

    optimization_run_id: Mapped[str] = mapped_column(ForeignKey("optimization_runs.id"), index=True)
    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)

    salary_used: Mapped[int] = mapped_column(Integer)
    salary_remaining: Mapped[int] = mapped_column(Integer)
    projected_points: Mapped[float] = mapped_column(Float)
    ceiling: Mapped[float] = mapped_column(Float)
    floor: Mapped[float] = mapped_column(Float)
    projected_ownership_product: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    stack_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stack_description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uniqueness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str | None] = mapped_column(String(2048), nullable=True)  # "Why this lineup?" panel text

    optimization_run: Mapped[OptimizationRun] = relationship(back_populates="lineups")
    players: Mapped[list["LineupPlayer"]] = relationship(back_populates="lineup")


class LineupPlayer(Base, UUIDPk, TimestampMixin):
    __tablename__ = "lineup_players"

    lineup_id: Mapped[str] = mapped_column(ForeignKey("lineups.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    roster_slot: Mapped[str] = mapped_column(String(8))  # QB, RB, WR, TE, FLEX, DST, CPT
    salary: Mapped[int] = mapped_column(Integer)
    projected_points: Mapped[float] = mapped_column(Float)
    is_locked: Mapped[bool] = mapped_column(default=False)

    lineup: Mapped[Lineup] = relationship(back_populates="players")
