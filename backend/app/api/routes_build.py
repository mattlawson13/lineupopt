"""The BUILD SLATE endpoint — spec section 18. Streams real-time progress
via Server-Sent Events so the frontend can render the checklist live.
"""
from __future__ import annotations

import dataclasses
import json

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.api.schemas import BuildSlateRequest
from app.db.session import SessionLocal
from app.ingestion.slate_builder import BuildOptions, run_build_slate

router = APIRouter(prefix="/api/slates", tags=["build"])


def _sse_stream(dk_draft_group_id: str | None, csv_text: str | None, options: BuildOptions):
    # A dedicated session is created and closed here (rather than via the
    # normal Depends(get_db) dependency) because Starlette begins
    # iterating a StreamingResponse's generator *after* the endpoint
    # function has already returned — a dependency-scoped session would
    # already be closed by then.
    db = SessionLocal()
    try:
        for event in run_build_slate(db, dk_draft_group_id=dk_draft_group_id, csv_text=csv_text, options=options):
            payload = json.dumps(dataclasses.asdict(event))
            yield f"data: {payload}\n\n"
    except Exception as exc:  # noqa: BLE001 — surface any unexpected failure to the client instead of a silent stream close
        db.rollback()
        payload = json.dumps({"step": "fatal_error", "status": "error", "detail": str(exc), "data": {}})
        yield f"data: {payload}\n\n"
    finally:
        db.close()


@router.post("/build")
def build_slate(req: BuildSlateRequest):
    """Build a slate live from DraftKings' API (no file upload needed)."""
    options = BuildOptions(
        num_simulations=req.num_simulations, num_lineups=req.num_lineups,
        objective=req.objective, seed=req.seed, dk_contest_id=req.dk_contest_id,
        max_player_exposure_pct=req.max_player_exposure_pct,
        max_captain_exposure_pct=req.max_captain_exposure_pct,
    )
    return StreamingResponse(
        _sse_stream(req.dk_draft_group_id, None, options), media_type="text/event-stream"
    )


@router.post("/build-from-csv")
async def build_slate_from_csv(
    file: UploadFile,
    dk_draft_group_id: str = Form("csv_import"),
    num_simulations: int = Form(10000),
    num_lineups: int = Form(20),
    objective: str = Form("large_field_gpp"),
    max_player_exposure_pct: float | None = Form(None),
    max_captain_exposure_pct: float | None = Form(None),
):
    """Build a slate from DraftKings' official salary-export CSV — the
    zero-scraping fallback path (spec section 2/4)."""
    csv_bytes = await file.read()
    options = BuildOptions(
        num_simulations=num_simulations, num_lineups=num_lineups, objective=objective,
        max_player_exposure_pct=max_player_exposure_pct, max_captain_exposure_pct=max_captain_exposure_pct,
    )
    return StreamingResponse(
        _sse_stream(dk_draft_group_id, csv_bytes.decode("utf-8"), options), media_type="text/event-stream"
    )
