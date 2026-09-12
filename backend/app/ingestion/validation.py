"""Data quality validation — spec section 30. Runs against raw DK slate
data before it reaches the projection/optimization layers, and against
generated projections before they reach the optimizer, so obviously
corrupt data never silently flows through the pipeline.
"""
from __future__ import annotations

import dataclasses

from app.data_sources.draftkings import DraftKingsSlate

VALID_NFL_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}
MAX_SANE_PROJECTION = {"QB": 55.0, "RB": 50.0, "WR": 50.0, "TE": 45.0, "K": 30.0, "DST": 35.0}


@dataclasses.dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    player_id: str | None = None


def validate_dk_slate(slate: DraftKingsSlate) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_ids: dict[str, int] = {}

    for row in slate.players:
        seen_ids[row.dk_player_id] = seen_ids.get(row.dk_player_id, 0) + 1

        if row.salary <= 0:
            issues.append(ValidationIssue("error", "missing_salary", f"{row.display_name}: salary is {row.salary}", row.dk_player_id))
        elif row.salary < 2000 or row.salary > 12000:
            issues.append(ValidationIssue("warning", "salary_outlier", f"{row.display_name}: unusual salary {row.salary}", row.dk_player_id))

        if row.position not in VALID_NFL_POSITIONS:
            issues.append(ValidationIssue("error", "invalid_position", f"{row.display_name}: invalid position {row.position!r}", row.dk_player_id))

        if not row.team_abbreviation:
            issues.append(ValidationIssue("error", "missing_team", f"{row.display_name}: no team", row.dk_player_id))

        if row.position != "DST" and not row.opponent_abbreviation:
            issues.append(ValidationIssue("warning", "missing_opponent", f"{row.display_name}: no opponent listed", row.dk_player_id))

    for dk_id, count in seen_ids.items():
        if count > 1:
            issues.append(ValidationIssue("warning", "duplicate_player", f"dk_player_id {dk_id} appears {count} times in slate", dk_id))

    if not slate.players:
        issues.append(ValidationIssue("error", "empty_slate", "Slate has zero players"))

    return issues


def validate_projection(player_id: str, name: str, position: str, salary: int, projected_points: float) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if projected_points < 0:
        issues.append(ValidationIssue("error", "negative_projection", f"{name}: negative projection {projected_points}", player_id))

    ceiling = MAX_SANE_PROJECTION.get(position, 50.0)
    if projected_points > ceiling:
        issues.append(ValidationIssue("warning", "projection_outlier", f"{name}: {projected_points} exceeds sane ceiling {ceiling} for {position}", player_id))

    if salary <= 0 and projected_points > 0:
        issues.append(ValidationIssue("error", "projection_without_salary", f"{name}: has a projection but salary is {salary}", player_id))

    return issues


def has_blocking_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.severity == "error" for i in issues)
