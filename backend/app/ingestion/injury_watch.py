"""Detects injury-status changes on slates that have already been built
but haven't started yet — user: "when dk gets rid of that info... we
should cache everything" led to proactive capture (see slate_builder.py's
capture_all_open_slates); this is the same proactive idea applied to
injury news instead of salary data. A build snapshots each player's
injury status into Projection.inputs at build time (see
_build_projections); this compares that snapshot against ESPN's current
designations and flags the slate when they've diverged.

Deliberately flags rather than auto-rebuilds. A rebuild costs real money
(player-prop odds quota, see data_sources/player_props.py) and processing
time, and would silently replace lineups a user might already be relying
on — surfacing "this changed, you may want to rebuild" and letting a
human decide is the same posture as everywhere else in this app that
touches real-money decisions (see resolution.py's false-positive-
resolution fix for the same principle in a different spot).
"""
from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.slate_builder import _fetch_injuries
from app.models.projections import Projection
from app.models.slate import DraftKingsPlayer, Slate
from app.normalization.player_matcher import normalize_name

_MAX_CHANGES_IN_DETAIL = 8


def check_injury_changes(db: Session) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    candidate_slates = db.execute(
        select(Slate).where(Slate.start_time_utc > now)
    ).scalars().all()

    # Only slates that have actually been built at least once (a build-time
    # injury snapshot to compare against) — a never-built slate has nothing
    # to detect drift from.
    slates_with_builds = []
    for slate in candidate_slates:
        has_projection = db.execute(
            select(Projection.id).where(Projection.slate_id == slate.id).limit(1)
        ).scalar_one_or_none()
        if has_projection:
            slates_with_builds.append(slate)

    if not slates_with_builds:
        return {"slates_checked": 0, "flagged": 0, "results": []}

    # One batched injury fetch across every watched slate's teams, rather
    # than one per slate — a Sunday main slate and a same-day Showdown
    # slate share most of their teams, and _fetch_injuries is already
    # rate-limited to ~1 team/second, so deduping matters.
    all_teams: set[str] = set()
    dk_rows_by_slate: dict[str, list[DraftKingsPlayer]] = {}
    for slate in slates_with_builds:
        dk_rows = db.execute(
            select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate.id)
        ).scalars().all()
        dk_rows_by_slate[slate.id] = dk_rows
        all_teams.update(r.team_abbreviation for r in dk_rows)

    current_status_by_norm_name, _warning = _fetch_injuries(db, all_teams)

    results = []
    for slate in slates_with_builds:
        projections = db.execute(
            select(Projection).where(Projection.slate_id == slate.id).order_by(Projection.created_at)
        ).scalars().all()
        # Ascending order — a later (more recent) build's row for the same
        # player overwrites an earlier one, so this ends up holding only
        # the most recent build's snapshot per player.
        build_time_status: dict[str, str] = {}
        for p in projections:
            status = (p.inputs or {}).get("injury_status")
            if status is not None:
                build_time_status[p.player_id] = status

        changes = []
        for dk_row in dk_rows_by_slate[slate.id]:
            if not dk_row.player_id or dk_row.player_id not in build_time_status:
                continue
            old_status = build_time_status[dk_row.player_id]
            new_status = current_status_by_norm_name.get(normalize_name(dk_row.display_name), "healthy")
            if old_status != new_status:
                changes.append((dk_row.display_name, old_status, new_status))

        if changes:
            detail = "; ".join(f"{name}: {old} → {new}" for name, old, new in changes[:_MAX_CHANGES_IN_DETAIL])
            if len(changes) > _MAX_CHANGES_IN_DETAIL:
                detail += f"; +{len(changes) - _MAX_CHANGES_IN_DETAIL} more"
            slate.injury_alert_detail = detail
            db.commit()
            results.append({"slate_id": slate.id, "name": slate.name, "status": "flagged", "changes": len(changes), "detail": detail})
        else:
            results.append({"slate_id": slate.id, "name": slate.name, "status": "unchanged"})

    return {
        "slates_checked": len(slates_with_builds),
        "flagged": sum(1 for r in results if r["status"] == "flagged"),
        "results": results,
    }
