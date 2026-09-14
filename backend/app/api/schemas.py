"""Pydantic request/response models for the API layer."""
from __future__ import annotations

from pydantic import BaseModel


class BuildSlateRequest(BaseModel):
    dk_draft_group_id: str | None = None
    num_simulations: int = 10000
    num_lineups: int = 20
    objective: str = "large_field_gpp"  # cash | single_entry | small_field_gpp | large_field_gpp
    seed: int | None = None
    # Optional: a specific DK contest ID to calibrate the objective against
    # (optimization/contest_calibration.py) — e.g. the exact contest you're
    # entering, so a 47,562-entry contest and a 500,000-entry contest don't
    # get treated identically just because both are "large_field_gpp".
    dk_contest_id: str | None = None


class ManualOptimizeRequest(BaseModel):
    slate_id: str
    num_lineups: int = 20
    objective: str = "large_field_gpp"
    dk_contest_id: str | None = None
    locked_player_ids: list[str] = []
    excluded_player_ids: list[str] = []
    # Lineup IDs (e.g. from an earlier build/generate call) the new batch
    # should stay meaningfully different from — same overlap rule the
    # portfolio already enforces on itself, applied against lineups you
    # already have so a fresh batch doesn't just reproduce them.
    exclude_lineup_ids: list[str] = []
    forced_team_min_counts: dict[str, int] = {}
    min_projection: float | None = None
    max_ownership_pct: float | None = None
    max_player_exposure_pct: float | None = None
    max_lineup_overlap: int | None = None
    seed: int | None = None


class ProjectionImportRequest(BaseModel):
    slate_id: str
    source_label: str
    csv_text: str


class InjuryImportRequest(BaseModel):
    player_id: str
    status: str
    description: str | None = None


class NewsImportRequest(BaseModel):
    player_id: str
    headline: str
    body: str | None = None
    source_url: str | None = None


class BettingImportRequest(BaseModel):
    game_id: str
    spread_home: float
    total: float
    book: str = "manual"
