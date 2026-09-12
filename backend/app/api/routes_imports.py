"""Manual import endpoints — the always-available fallback path for any
source that can't be reliably pulled live (spec section 2/4/39): paid
projection providers, blocked injury feeds, sportsbook lines without an
API key configured.
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import BettingImportRequest, InjuryImportRequest, NewsImportRequest, ProjectionImportRequest
from app.data_sources.base import SourceUnavailableError
from app.data_sources.projection_import import ManualProjectionImportSource
from app.features.game_environment import compute_game_environment
from app.models.context_data import BettingLine, PlayerInjury, PlayerNews
from app.models.core import Game, Player
from app.models.projections import Projection, ProjectionSource
from app.normalization.player_matcher import normalize_name

router = APIRouter(prefix="/api", tags=["imports"])


@router.post("/injuries/import")
def import_injury(req: InjuryImportRequest, db: Session = Depends(get_db)):
    player = db.get(Player, req.player_id)
    if not player:
        raise HTTPException(404, "Player not found")
    row = PlayerInjury(
        player_id=player.id, season=datetime.date.today().year, week=0, status=req.status,
        description=req.description, source="manual", reported_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(row)
    db.commit()
    return {"ok": True}


@router.post("/news/import")
def import_news(req: NewsImportRequest, db: Session = Depends(get_db)):
    player = db.get(Player, req.player_id)
    if not player:
        raise HTTPException(404, "Player not found")
    row = PlayerNews(
        player_id=player.id, headline=req.headline, body=req.body, source="manual",
        source_url=req.source_url, published_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(row)
    db.commit()
    return {"ok": True}


@router.post("/betting/import")
def import_betting_line(req: BettingImportRequest, db: Session = Depends(get_db)):
    game = db.get(Game, req.game_id)
    if not game:
        raise HTTPException(404, "Game not found")
    env = compute_game_environment(req.spread_home, req.total)
    row = BettingLine(
        game_id=game.id, book=req.book, spread_home=req.spread_home, total=req.total,
        implied_total_home=env.home_implied_total, implied_total_away=env.away_implied_total,
    )
    db.add(row)
    db.commit()
    return {"ok": True, "implied_total_home": env.home_implied_total, "implied_total_away": env.away_implied_total}


@router.post("/projections/import")
def import_projections(req: ProjectionImportRequest, db: Session = Depends(get_db)):
    """Imports a third-party projection CSV as `source_projection_1/2/3` —
    does NOT rebuild the ensemble automatically; re-run /api/slates/build
    (or a future incremental re-ensemble endpoint) to fold it in.
    """
    try:
        source = ManualProjectionImportSource()
        result = source.parse_projection_csv(req.csv_text, req.source_label)
    except SourceUnavailableError as exc:
        raise HTTPException(400, str(exc)) from exc

    proj_source = db.query(ProjectionSource).filter(ProjectionSource.name == f"manual_import:{req.source_label}").first()
    if not proj_source:
        proj_source = ProjectionSource(name=f"manual_import:{req.source_label}", kind="manual_import", default_confidence=0.75)
        db.add(proj_source)
        db.flush()

    matched, unmatched = 0, []
    for row in result.data:
        norm = normalize_name(row.player_name)
        player = db.query(Player).filter(Player.normalized_name == norm).first()
        if not player:
            unmatched.append(row.player_name)
            continue
        matched += 1
        db.add(Projection(
            slate_id=req.slate_id, player_id=player.id, source_id=proj_source.id,
            projected_points=row.projection, floor=row.floor, median=row.projection, ceiling=row.ceiling,
            confidence=proj_source.default_confidence, inputs={}, component_breakdown={},
        ))
    db.commit()

    return {
        "rows_parsed": len(result.data), "matched": matched, "unmatched": unmatched[:20],
        "warnings": result.warnings,
        "note": "Stored as a raw Projection row (source_projection slot). Re-run /api/slates/build to fold it into the ensemble.",
    }
