from __future__ import annotations

import enum


class Sport(str, enum.Enum):
    NFL = "nfl"
    SOCCER = "soccer"


class Position(str, enum.Enum):
    # NFL
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DST = "DST"
    FLEX = "FLEX"
    CPT = "CPT"
    # Soccer
    GK = "GK"
    D = "D"
    M = "M"
    F = "F"


class InjuryStatus(str, enum.Enum):
    HEALTHY = "healthy"
    QUESTIONABLE = "questionable"
    DOUBTFUL = "doubtful"
    OUT = "out"
    IR = "ir"
    PUP = "pup"
    SUSPENDED = "suspended"


class ContestType(str, enum.Enum):
    CLASSIC = "classic"
    SHOWDOWN = "showdown"


class SourceConfidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    STALE = "stale"


class OptimizationObjective(str, enum.Enum):
    CASH = "cash"
    SINGLE_ENTRY = "single_entry"
    SMALL_FIELD_GPP = "small_field_gpp"
    LARGE_FIELD_GPP = "large_field_gpp"


class StackType(str, enum.Enum):
    NAKED_QB = "naked_qb"
    STANDARD = "standard"
    DOUBLE_STACK = "double_stack"
    BRING_BACK = "bring_back"
    GAME_STACK = "game_stack"
    RUN_BACK = "run_back"


class GameScript(str, enum.Enum):
    CLOSE_HIGH_SCORING = "close_high_scoring"
    HOME_BLOWOUT = "home_blowout"
    AWAY_BLOWOUT = "away_blowout"
    LOW_SCORING = "low_scoring"
    COMPETITIVE_SHOOTOUT = "competitive_shootout"
    DEFENSIVE_GAME = "defensive_game"
