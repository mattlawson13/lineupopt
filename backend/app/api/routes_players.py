from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.correlations.engine import CorrelationEntry
from app.models.analytics import Correlation, OwnershipProjection, PlayerSimulationResult, SimulationRun
from app.models.core import Player
from app.models.projections import EnsembleProjection

router = APIRouter(prefix="/api/players", tags=["players"])


@router.get("/{player_id}")
def get_player_detail(player_id: str, slate_id: str, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404, "Player not found")

    ensemble = db.execute(
        select(EnsembleProjection)
        .where(EnsembleProjection.slate_id == slate_id, EnsembleProjection.player_id == player_id)
        .order_by(EnsembleProjection.created_at.desc())
    ).scalars().first()
    ownership = db.execute(
        select(OwnershipProjection)
        .where(OwnershipProjection.slate_id == slate_id, OwnershipProjection.player_id == player_id)
        .order_by(OwnershipProjection.created_at.desc())
    ).scalars().first()
    latest_sim = db.execute(
        select(SimulationRun).where(SimulationRun.slate_id == slate_id).order_by(SimulationRun.completed_at.desc())
    ).scalars().first()
    sim_result = None
    if latest_sim:
        sim_result = db.execute(
            select(PlayerSimulationResult).where(
                PlayerSimulationResult.simulation_run_id == latest_sim.id, PlayerSimulationResult.player_id == player_id
            )
        ).scalar_one_or_none()

    correlations = db.execute(
        select(Correlation).where(
            Correlation.slate_id == slate_id,
            (Correlation.player_a_id == player_id) | (Correlation.player_b_id == player_id),
        )
    ).scalars().all()
    best_stacks = sorted(correlations, key=lambda c: c.correlation, reverse=True)[:5]
    worst_correlations = sorted(correlations, key=lambda c: c.correlation)[:5]

    def _other_player(c: Correlation) -> str:
        other_id = c.player_b_id if c.player_a_id == player_id else c.player_a_id
        other = db.get(Player, other_id)
        return other.full_name if other else other_id

    return {
        "player_id": player.id, "name": player.full_name, "position": player.position,
        "team": player.team.abbreviation if player.team else None,
        "why_panel": ensemble.component_breakdown if ensemble else None,
        "ensemble": {
            "ensemble_projection": ensemble.ensemble_projection, "floor": ensemble.floor,
            "median": ensemble.median, "ceiling": ensemble.ceiling, "std_dev": ensemble.std_dev,
            "weights_used": ensemble.weights_used,
        } if ensemble else None,
        "ownership": {
            "projected_ownership_pct": ownership.projected_ownership_pct,
            "chalk_score": ownership.chalk_score, "contrarian_score": ownership.contrarian_score,
            "feature_breakdown": ownership.feature_breakdown,
        } if ownership else None,
        "simulation": {
            "mean": sim_result.mean, "median": sim_result.median, "std_dev": sim_result.std_dev,
            "floor": sim_result.floor, "ceiling": sim_result.ceiling, "percentiles": sim_result.percentiles,
            "prob_3x_salary": sim_result.prob_3x_salary, "prob_4x_salary": sim_result.prob_4x_salary,
            "prob_5x_salary": sim_result.prob_5x_salary, "prob_6x_salary": sim_result.prob_6x_salary,
            "prob_top1pct": sim_result.prob_top1pct, "prob_top5pct": sim_result.prob_top5pct,
        } if sim_result else None,
        "best_stacks": [
            {"with": _other_player(c), "relationship": c.relationship_type, "correlation": c.correlation, "is_empirical": c.is_empirical}
            for c in best_stacks
        ],
        "worst_correlations": [
            {"with": _other_player(c), "relationship": c.relationship_type, "correlation": c.correlation, "is_empirical": c.is_empirical}
            for c in worst_correlations
        ],
    }
