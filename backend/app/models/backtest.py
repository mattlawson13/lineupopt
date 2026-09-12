from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPk


class BacktestResult(Base, UUIDPk, TimestampMixin):
    """One predicted-vs-actual row per player per slate per model version.
    The backtesting engine (backtesting/engine.py) aggregates these into
    MAE/RMSE/bias/correlation/ROI by position, salary tier, game total,
    and ownership bucket — see spec sections 23-24.
    """

    __tablename__ = "backtest_results"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"), index=True)

    position: Mapped[str] = mapped_column(String(8), index=True)
    salary: Mapped[int] = mapped_column(Integer)
    game_total: Mapped[float | None] = mapped_column(Float, nullable=True)

    predicted_points: Mapped[float] = mapped_column(Float)
    actual_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_ownership_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_ownership_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    lineup_id: Mapped[str | None] = mapped_column(ForeignKey("lineups.id"), nullable=True)
    lineup_actual_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    lineup_roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    contest_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)

    error_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
