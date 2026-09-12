"""Time-series contextual data: stats, injuries, news, weather, betting.

Every row carries `source` + `fetched_at` (via TimestampMixin.created_at)
so staleness and provenance are always inspectable — see spec section 30.
"""
from __future__ import annotations

import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPk


class PlayerStat(Base, UUIDPk, TimestampMixin):
    """Actual final stat line for a player in a completed game. This is the
    ground truth used for both feature-building (recent usage) and
    backtesting (predicted vs actual).
    """

    __tablename__ = "player_stats"

    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer, index=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)  # component stat dict, position-specific keys
    dk_fantasy_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="nflverse")


class PlayerSnapshot(Base, UUIDPk, TimestampMixin):
    """Point-in-time usage snapshot (rolling averages as of a given date),
    computed by features/usage_features.py. Distinct from PlayerStat
    (single-game actuals) — this is the derived feature the model consumes.
    """

    __tablename__ = "player_snapshots"

    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    as_of: Mapped[datetime.datetime] = mapped_column(index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer, index=True)
    games_sampled: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    # e.g. {"snap_pct": 0.71, "target_share": 0.24, "route_pct": 0.83,
    #       "carry_share": 0.0, "redzone_share": 0.18, "aDOT": 9.4, ...}


class PlayerInjury(Base, UUIDPk, TimestampMixin):
    __tablename__ = "player_injuries"

    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))  # InjuryStatus value
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    practice_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="espn")
    reported_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class PlayerNews(Base, UUIDPk, TimestampMixin):
    __tablename__ = "player_news"

    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    headline: Mapped[str] = mapped_column(String(512))
    body: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    impact: Mapped[str | None] = mapped_column(String(16), nullable=True)  # positive | negative | neutral
    source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime.datetime]


class Weather(Base, UUIDPk, TimestampMixin):
    __tablename__ = "weather"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    temperature_f: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    precipitation_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    precipitation_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # rain | snow | none
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_forecast: Mapped[bool] = mapped_column(default=True)  # forecast until kickoff, then actual
    source: Mapped[str] = mapped_column(String(32), default="open_meteo")
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class BettingLine(Base, UUIDPk, TimestampMixin):
    __tablename__ = "betting_lines"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    book: Mapped[str] = mapped_column(String(64), default="consensus")
    spread_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyline_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    moneyline_away: Mapped[int | None] = mapped_column(Integer, nullable=True)
    implied_total_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_total_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="odds_api")
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
