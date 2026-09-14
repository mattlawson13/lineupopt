"""Slate, contest, and DraftKings-specific player/salary data."""
from __future__ import annotations

import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPk


class Slate(Base, UUIDPk, TimestampMixin):
    """A DraftKings 'draft group' — a specific slate of games/players."""

    __tablename__ = "slates"

    sport: Mapped[str] = mapped_column(String(16), index=True)
    contest_type: Mapped[str] = mapped_column(String(16), default="classic")
    dk_draft_group_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    name: Mapped[str] = mapped_column(String(256))
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    start_time_utc: Mapped[datetime.datetime]
    game_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # ordered list of games.id in this slate
    source: Mapped[str] = mapped_column(String(32), default="dk_api")  # dk_api | csv_import
    imported_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    # Set by ingestion/injury_watch.py when a player's injury designation
    # has changed since this slate was last built — non-null means "a
    # rebuild would likely change these projections." Cleared whenever the
    # slate is (re)built, since a fresh build captures current injury
    # status anyway. Never auto-rebuilds: a rebuild costs real money (prop
    # odds quota) and processing time, and silently replacing lineups a
    # user may already be relying on would be a bad surprise — this is
    # strictly a "you should probably rebuild" flag for a human to act on.
    injury_alert_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    dk_players: Mapped[list["DraftKingsPlayer"]] = relationship(back_populates="slate")
    contests: Mapped[list["Contest"]] = relationship(back_populates="slate")


class DraftKingsPlayer(Base, UUIDPk, TimestampMixin):
    """A player's DK-specific listing on a given slate (salary, position
    eligibility, roster status) — never a source of truth for identity;
    joins to `players` via player_id, resolved by normalization/player_matcher.
    """

    __tablename__ = "draftkings_players"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)
    dk_player_id: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    dk_position: Mapped[str] = mapped_column(String(8))
    team_abbreviation: Mapped[str] = mapped_column(String(8))
    opponent_abbreviation: Mapped[str] = mapped_column(String(8))
    game_id: Mapped[str | None] = mapped_column(ForeignKey("games.id"), nullable=True)
    roster_status: Mapped[str] = mapped_column(String(32), default="active")  # active, questionable, out, scratched
    is_starting: Mapped[bool | None] = mapped_column(nullable=True)

    slate: Mapped[Slate] = relationship(back_populates="dk_players")
    salaries: Mapped[list["DraftKingsSalary"]] = relationship(back_populates="dk_player")


class DraftKingsSalary(Base, UUIDPk, TimestampMixin):
    """Salary is versioned separately from the player listing because DK
    sometimes adjusts salaries intra-week; keep history rather than
    overwriting.
    """

    __tablename__ = "draftkings_salaries"

    dk_player_id_fk: Mapped[str] = mapped_column(ForeignKey("draftkings_players.id"), index=True)
    salary: Mapped[int] = mapped_column(Integer)
    effective_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    dk_player: Mapped[DraftKingsPlayer] = relationship(back_populates="salaries")


class Contest(Base, UUIDPk, TimestampMixin):
    __tablename__ = "contests"

    slate_id: Mapped[str] = mapped_column(ForeignKey("slates.id"), index=True)
    dk_contest_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    name: Mapped[str] = mapped_column(String(256))
    contest_type: Mapped[str] = mapped_column(String(16), default="classic")
    entry_fee: Mapped[float] = mapped_column(Float, default=0.0)
    total_prizes: Mapped[float] = mapped_column(Float, default=0.0)
    max_entries: Mapped[int] = mapped_column(Integer, default=1)
    max_entries_per_user: Mapped[int] = mapped_column(Integer, default=1)
    entries: Mapped[int] = mapped_column(Integer, default=0)
    payout_structure: Mapped[dict] = mapped_column(JSON, default=dict)  # {place: payout}
    is_guaranteed: Mapped[bool] = mapped_column(default=False)

    slate: Mapped[Slate] = relationship(back_populates="contests")
