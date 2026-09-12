"""Resolves DraftKings roster/salary rules from config into a shape the
ILP optimizer can consume directly. Nothing here is hardcoded — see
config/dk_roster_rules_nfl.yaml.
"""
from __future__ import annotations

import dataclasses

from app.config.loader import get_dk_roster_rules


@dataclasses.dataclass
class RosterSlot:
    name: str
    eligible_positions: list[str]
    count: int
    salary_multiplier: float = 1.0
    score_multiplier: float = 1.0


@dataclasses.dataclass
class ContestRules:
    salary_cap: int
    roster_size: int
    slots: list[RosterSlot]
    max_players_per_team: int
    min_teams_represented: int
    min_games_represented: int


def get_contest_rules(sport: str = "nfl", contest_type: str = "classic") -> ContestRules:
    cfg = get_dk_roster_rules(sport, contest_type)
    slots = [
        RosterSlot(
            name=slot_name,
            eligible_positions=info["eligible"],
            count=info["count"],
            salary_multiplier=info.get("salary_multiplier", 1.0),
            score_multiplier=info.get("score_multiplier", 1.0),
        )
        for slot_name, info in cfg["positions"].items()
    ]
    return ContestRules(
        salary_cap=cfg["salary_cap"],
        roster_size=cfg["roster_size"],
        slots=slots,
        max_players_per_team=cfg["constraints"]["max_players_per_team"],
        min_teams_represented=cfg["constraints"]["min_teams_represented"],
        min_games_represented=cfg["constraints"]["min_games_represented"],
    )
