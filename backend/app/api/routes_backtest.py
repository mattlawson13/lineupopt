from __future__ import annotations

import dataclasses

from fastapi import APIRouter, HTTPException

from app.backtesting.engine import run_backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("")
def get_backtest(season: int, weeks: str):
    """`weeks` is a comma-separated list, e.g. "1,2,3"."""
    try:
        week_list = [int(w) for w in weeks.split(",") if w.strip()]
    except ValueError as exc:
        raise HTTPException(400, "weeks must be a comma-separated list of integers") from exc

    try:
        rows, summary = run_backtest(season, week_list)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "n": len(rows),
        "overall": dataclasses.asdict(summary.overall),
        "by_position": {k: dataclasses.asdict(v) for k, v in summary.by_position.items()},
        "by_usage_tier": {k: dataclasses.asdict(v) for k, v in summary.by_usage_tier.items()},
        "rows": [dataclasses.asdict(r) for r in rows],
    }
