"""Core sport-agnostic entities: teams, players, games."""
from __future__ import annotations

import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPk


class Team(Base, UUIDPk, TimestampMixin):
    __tablename__ = "teams"

    sport: Mapped[str] = mapped_column(String(16), index=True)
    abbreviation: Mapped[str] = mapped_column(String(8), index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)  # {"espn": "...", "nflverse": "..."}

    players: Mapped[list["Player"]] = relationship(back_populates="team")


class Player(Base, UUIDPk, TimestampMixin):
    __tablename__ = "players"

    sport: Mapped[str] = mapped_column(String(16), index=True)
    full_name: Mapped[str] = mapped_column(String(128), index=True)
    normalized_name: Mapped[str] = mapped_column(String(128), index=True)  # lowercase, no suffixes/punctuation, for cross-source matching
    position: Mapped[str] = mapped_column(String(8), index=True)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    birthdate: Mapped[datetime.date | None] = mapped_column(nullable=True)
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)  # {"dk": "...", "espn": "...", "gsis_id": "...", "nflverse": "..."}
    active: Mapped[bool] = mapped_column(default=True)

    team: Mapped[Team | None] = relationship(back_populates="players")


class Game(Base, UUIDPk, TimestampMixin):
    __tablename__ = "games"

    sport: Mapped[str] = mapped_column(String(16), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer, index=True)
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    kickoff_utc: Mapped[datetime.datetime] = mapped_column(index=True)
    venue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_dome: Mapped[bool] = mapped_column(default=False)
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)

    # Final actuals, once the game has been played (used for backtesting).
    home_score_final: Mapped[int | None] = mapped_column(nullable=True)
    away_score_final: Mapped[int | None] = mapped_column(nullable=True)

    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
