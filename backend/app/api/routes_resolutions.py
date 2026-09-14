"""Cross-slate pattern mining over resolved slates' retro-optimal lineups
— user: "what were the 5 best lineups for all Showdown/Classic contests
so far, and what patterns show up (QB stacked with 2 WR, etc.)?" This is
that, built from real, resolved slates we already have (see
ingestion/resolution.py) rather than needing DraftKings' actual contest
results — we already have the stats, the DK scoring formula, and the
optimizer; a slate's retro-optimal lineup already *is* the best roster
obtainable for that slate with perfect hindsight.

Necessarily gets more reliable as more slates get resolved. Sample sizes
are reported explicitly rather than implying confidence prematurely —
this is descriptive/directional, not (yet) fed back into the optimizer's
own defaults automatically; see the module docstring in
ingestion/resolution.py for why that's a deliberate, separate next step.
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.ingestion.resolution import ResolutionUnavailableError, resolve_slate
from app.models.analytics import SlateResolution
from app.models.lineup import Lineup
from app.models.slate import Slate
from app.optimization.dk_rules import get_contest_rules

router = APIRouter(prefix="/api/resolutions", tags=["resolutions"])


@router.post("/resolve_all")
def resolve_all_slates(db: Session = Depends(get_db)):
    """Resolves every slate this app has ever ingested (has real DK
    player/salary data for — see Slate rows created by a Build) that has
    final stats available, in one call — user: "it looks at ALL the
    slates... and then uses that data," instead of clicking Resolve on
    one slate at a time. Only ingested slates are in scope: this can't
    resolve a DK slate the app was never pointed at, since there's no
    player pool/salary data to grade against.

    Idempotent by skipping any slate that already has a SlateResolution
    row rather than piling up duplicates on repeated calls — a slate
    needing re-grading (e.g. a stat correction) still goes through the
    single-slate POST /api/slates/{slate_id}/resolve directly.
    """
    slates = db.execute(select(Slate)).scalars().all()
    already_resolved_ids = {row[0] for row in db.execute(select(SlateResolution.slate_id).distinct()).all()}

    results = []
    for slate in slates:
        if slate.id in already_resolved_ids:
            results.append({"slate_id": slate.id, "name": slate.name, "status": "skipped", "detail": "already resolved"})
            continue
        try:
            summary = resolve_slate(db, slate.id)
            results.append({
                "slate_id": slate.id, "name": slate.name, "status": "resolved",
                "retro_optimal_points": summary.retro_optimal_points,
                "our_best_actual_points": summary.our_best_actual_points,
                "players_resolved": summary.players_resolved,
            })
        except ResolutionUnavailableError as exc:
            db.rollback()
            results.append({"slate_id": slate.id, "name": slate.name, "status": "unavailable", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 — one slate failing shouldn't stop the rest of the batch
            db.rollback()
            results.append({"slate_id": slate.id, "name": slate.name, "status": "error", "detail": str(exc)})

    return {
        "total_slates": len(slates),
        "resolved": sum(1 for r in results if r["status"] == "resolved"),
        "skipped_already_resolved": sum(1 for r in results if r["status"] == "skipped"),
        "unavailable": sum(1 for r in results if r["status"] == "unavailable"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }


@router.get("/patterns")
def get_resolution_patterns(db: Session = Depends(get_db)):
    resolutions = db.execute(select(SlateResolution)).scalars().all()

    by_contest_type: dict[str, dict] = {}
    for res in resolutions:
        if not res.retro_optimal_run_id:
            continue
        # All ~5 retro-optimal lineups for this slate, not just the single
        # best one — more data points per slate for the stack/salary
        # distribution below, per user request ("runs the top 5 lineups
        # for each slate... now we see patterns").
        lineups = db.execute(
            select(Lineup).where(Lineup.optimization_run_id == res.retro_optimal_run_id)
        ).scalars().all()
        slate = db.get(Slate, res.slate_id)
        if not lineups or not slate:
            continue
        cap = get_contest_rules("nfl", slate.contest_type).salary_cap
        bucket = by_contest_type.setdefault(slate.contest_type, {
            "slate_ids": set(), "salary_used_pcts": [], "stack_types": Counter(), "mae_values": [], "bias_values": [],
        })
        bucket["slate_ids"].add(slate.id)
        bucket["mae_values"].append(res.mae)
        bucket["bias_values"].append(res.bias)
        for lineup in lineups:
            bucket["salary_used_pcts"].append(lineup.salary_used / cap * 100)
            bucket["stack_types"][lineup.stack_type or "none"] += 1

    out = {}
    for contest_type, bucket in by_contest_type.items():
        n_slates = len(bucket["slate_ids"])
        n_lineups = len(bucket["salary_used_pcts"])
        out[contest_type] = {
            "slates_resolved": n_slates,
            "retro_optimal_lineups_analyzed": n_lineups,
            "avg_salary_used_pct": round(sum(bucket["salary_used_pcts"]) / n_lineups, 1),
            "stack_type_distribution": {
                k: {"count": v, "pct": round(v / n_lineups * 100, 1)} for k, v in bucket["stack_types"].most_common()
            },
            "avg_projection_mae": round(sum(bucket["mae_values"]) / n_slates, 2),
            "avg_projection_bias": round(sum(bucket["bias_values"]) / n_slates, 2),
        }

    return {
        "note": (
            "Patterns mined from this app's own retro-optimal (best-possible-with-hindsight) "
            "lineups across every slate resolved so far — real stats, real DK scoring, our own "
            "optimizer, not external contest data. Small sample sizes are expected early on: "
            "treat these as directional until slates_resolved is large enough to trust."
        ),
        "by_contest_type": out,
    }
