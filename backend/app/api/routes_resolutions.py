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
from app.models.analytics import SlateResolution
from app.models.lineup import Lineup
from app.models.slate import Slate
from app.optimization.dk_rules import get_contest_rules

router = APIRouter(prefix="/api/resolutions", tags=["resolutions"])


@router.get("/patterns")
def get_resolution_patterns(db: Session = Depends(get_db)):
    resolutions = db.execute(select(SlateResolution)).scalars().all()

    by_contest_type: dict[str, dict] = {}
    for res in resolutions:
        if not res.retro_optimal_lineup_id:
            continue
        lineup = db.get(Lineup, res.retro_optimal_lineup_id)
        slate = db.get(Slate, res.slate_id)
        if not lineup or not slate:
            continue
        cap = get_contest_rules("nfl", slate.contest_type).salary_cap
        bucket = by_contest_type.setdefault(slate.contest_type, {
            "count": 0, "salary_used_pcts": [], "stack_types": Counter(), "mae_values": [], "bias_values": [],
        })
        bucket["count"] += 1
        bucket["salary_used_pcts"].append(lineup.salary_used / cap * 100)
        bucket["stack_types"][lineup.stack_type or "none"] += 1
        bucket["mae_values"].append(res.mae)
        bucket["bias_values"].append(res.bias)

    out = {}
    for contest_type, bucket in by_contest_type.items():
        n = bucket["count"]
        out[contest_type] = {
            "slates_resolved": n,
            "avg_salary_used_pct": round(sum(bucket["salary_used_pcts"]) / n, 1),
            "stack_type_distribution": {
                k: {"count": v, "pct": round(v / n * 100, 1)} for k, v in bucket["stack_types"].most_common()
            },
            "avg_projection_mae": round(sum(bucket["mae_values"]) / n, 2),
            "avg_projection_bias": round(sum(bucket["bias_values"]) / n, 2),
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
