"""Projection ensemble models — see spec section 3.

Every player/slate combination gets one `Projection` row per source per
model version, so the full history of `source_projection_1..3`,
`market_projection`, `model_projection`, and the final `ensemble_projection`
is reproducible and backtestable.
"""
from __future__ import annotations

import datetime

from sqlalchemy import JSON, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPk


class ProjectionSource(Base, UUIDPk, TimestampMixin):
    """Registry of projection providers (our own model, market-derived,
    manually-imported third parties). Confidence is configurable and can be
    tuned over time via backtesting.
    """

    __tablename__ = "projection_sources"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32))  # model | market | manual_import | api
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_confidence: Mapped[float] = mapped_column(Float, default=0.85)
    active: Mapped[bool] = mapped_column(default=True)


class Projection(Base, UUIDPk, TimestampMixin):
    __tablename__ = "projections"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    dk_player_id_fk: Mapped[str | None] = mapped_column(ForeignKey("draftkings_players.id"), nullable=True, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("projection_sources.id"), index=True)
    model_version_id: Mapped[str | None] = mapped_column(ForeignKey("model_versions.id"), nullable=True)

    projected_points: Mapped[float] = mapped_column(Float)
    floor: Mapped[float | None] = mapped_column(Float, nullable=True)
    median: Mapped[float | None] = mapped_column(Float, nullable=True)
    ceiling: Mapped[float | None] = mapped_column(Float, nullable=True)
    std_dev: Mapped[float | None] = mapped_column(Float, nullable=True)

    confidence: Mapped[float] = mapped_column(Float, default=0.85)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)      # feature snapshot used to produce this projection
    component_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)  # the "Why?" panel adjustments, see spec section 34
    generated_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class EnsembleProjection(Base, UUIDPk, TimestampMixin):
    """The final weighted-consensus projection for a player on a slate —
    one row per player per slate per model_version, distinct from the raw
    per-source `Projection` rows it was built from.
    """

    __tablename__ = "ensemble_projections"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"))

    source_projection_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_projection_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_projection_3: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_projection: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_projection: Mapped[float | None] = mapped_column(Float, nullable=True)
    ensemble_projection: Mapped[float] = mapped_column(Float)

    floor: Mapped[float] = mapped_column(Float)
    median: Mapped[float] = mapped_column(Float)
    ceiling: Mapped[float] = mapped_column(Float)
    std_dev: Mapped[float] = mapped_column(Float)
    weights_used: Mapped[dict] = mapped_column(JSON, default=dict)
    component_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
