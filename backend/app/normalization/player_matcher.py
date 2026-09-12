"""Cross-source player/team identity resolution — spec section 3
("normalize projections from multiple sources").

DK, nflverse, ESPN, and manually-imported CSVs each spell names and team
codes slightly differently (suffixes, punctuation, "JAX" vs "JAC"). This
module is the single place that reconciles them into one `Player`/`Team`
row so a projection imported from one source lands on the same player
DraftKings lists.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import Player, Team

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\b", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# A handful of team abbreviation aliases that differ across sources.
TEAM_ABBREV_ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV",
}


def normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = _SUFFIX_RE.sub("", name)
    name = _PUNCT_RE.sub("", name)
    name = _WS_RE.sub(" ", name).strip()
    return name


def normalize_team_abbreviation(abbrev: str) -> str:
    abbrev = abbrev.upper().strip()
    return TEAM_ABBREV_ALIASES.get(abbrev, abbrev)


def get_or_create_team(db: Session, sport: str, abbreviation: str, full_name: str = "") -> Team:
    abbrev = normalize_team_abbreviation(abbreviation)
    team = db.execute(
        select(Team).where(Team.sport == sport, Team.abbreviation == abbrev)
    ).scalar_one_or_none()
    if team:
        return team
    team = Team(sport=sport, abbreviation=abbrev, full_name=full_name or abbrev, external_ids={})
    db.add(team)
    db.flush()
    return team


def get_or_create_player(
    db: Session, sport: str, full_name: str, position: str, team: Team | None, dk_player_id: str | None = None
) -> Player:
    norm = normalize_name(full_name)

    query = select(Player).where(Player.sport == sport, Player.normalized_name == norm, Player.position == position)
    if team is not None:
        query = query.where(Player.team_id == team.id)
    player = db.execute(query).scalar_one_or_none()

    if player is None and dk_player_id:
        # Fall back to matching by a previously-recorded DK id, in case the
        # player's team changed (trade) since we last saw them.
        candidates = db.execute(
            select(Player).where(Player.sport == sport, Player.normalized_name == norm, Player.position == position)
        ).scalars().all()
        for c in candidates:
            if c.external_ids.get("dk") == dk_player_id:
                player = c
                break

    if player is not None:
        if dk_player_id and player.external_ids.get("dk") != dk_player_id:
            player.external_ids = {**player.external_ids, "dk": dk_player_id}
        if team is not None and player.team_id != team.id:
            player.team_id = team.id
        return player

    player = Player(
        sport=sport,
        full_name=full_name,
        normalized_name=norm,
        position=position,
        team_id=team.id if team else None,
        external_ids={"dk": dk_player_id} if dk_player_id else {},
    )
    db.add(player)
    db.flush()
    return player
