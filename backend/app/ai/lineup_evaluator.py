"""AI lineup evaluation & explanation layer — spec section 17.

This is a SELECTION AND ANALYSIS layer, not a second optimizer: it never
adds/removes/swaps a player (that would let it "arbitrarily override
mathematical constraints", which the spec explicitly forbids). It only
ranks the lineups the ILP optimizer already produced and explains, in
plain language, why each one is built the way it is — grounded entirely in
the structured projection/ownership/simulation output already computed,
never invented.

When ANTHROPIC_API_KEY is configured, `generate_llm_narrative()` can be
used to phrase the explanation more naturally — but the underlying ranking
and every fact it references still comes from this deterministic pass, so
the AI can never fabricate a statistic (spec section 36).
"""
from __future__ import annotations

import statistics

from sqlalchemy.orm import Session

from app.models.analytics import OwnershipProjection
from app.models.lineup import Lineup

RANK_WEIGHTS = {"projection": 0.30, "ceiling": 0.30, "contrarian": 0.20, "uniqueness": 0.10, "stack": 0.10}
LOW_OWNED_THRESHOLD_PCT = 10.0
CHALK_THRESHOLD_PCT = 25.0


def evaluate_and_rank_lineups(
    db: Session, lineups: list[Lineup], ownership_by_player_id: dict[str, OwnershipProjection]
) -> None:
    if not lineups:
        return

    player_sets = {lu.id: {lp.player_id for lp in lu.players} for lu in lineups}
    proj_values = [lu.projected_points for lu in lineups]
    ceiling_values = [lu.ceiling for lu in lineups]

    proj_max = max(proj_values) or 1.0
    ceiling_max = max(ceiling_values) or 1.0

    scored: list[tuple[Lineup, float, str]] = []
    for lu in lineups:
        own_set = player_sets[lu.id]
        diffs = [len(own_set - player_sets[other.id]) for other in lineups if other.id != lu.id]
        uniqueness = sum(diffs) / len(diffs) if diffs else float(len(own_set))
        lu.uniqueness_score = round(uniqueness, 2)

        own_pcts = [ownership_by_player_id[lp.player_id].projected_ownership_pct for lp in lu.players if lp.player_id in ownership_by_player_id]
        contrarian_scores = [ownership_by_player_id[lp.player_id].contrarian_score or 0 for lp in lu.players if lp.player_id in ownership_by_player_id]
        avg_ownership = round(statistics.mean(own_pcts), 2) if own_pcts else 0.0
        avg_contrarian = statistics.mean(contrarian_scores) if contrarian_scores else 50.0
        lu.projected_ownership_product = avg_ownership
        lu.leverage_score = round(avg_contrarian - 50.0, 2)  # positive = net contrarian build

        stack_bonus = 1.0 if lu.stack_type and lu.stack_type != "naked_qb" else 0.4

        score = (
            RANK_WEIGHTS["projection"] * (lu.projected_points / proj_max)
            + RANK_WEIGHTS["ceiling"] * (lu.ceiling / ceiling_max)
            + RANK_WEIGHTS["contrarian"] * (avg_contrarian / 100.0)
            + RANK_WEIGHTS["uniqueness"] * min(uniqueness / 9.0, 1.0)
            + RANK_WEIGHTS["stack"] * stack_bonus
        )

        explanation = _build_explanation(lu, ownership_by_player_id, uniqueness, avg_ownership)
        scored.append((lu, round(score, 4), explanation))

    scored.sort(key=lambda t: t[1], reverse=True)
    for rank, (lu, score, explanation) in enumerate(scored, start=1):
        lu.ai_rank = rank
        lu.ai_score = score
        lu.explanation = explanation


def _build_explanation(
    lu: Lineup, ownership_by_player_id: dict[str, OwnershipProjection], uniqueness: float, avg_ownership: float
) -> str:
    low_owned = [
        lp for lp in lu.players
        if lp.player_id in ownership_by_player_id and ownership_by_player_id[lp.player_id].projected_ownership_pct < LOW_OWNED_THRESHOLD_PCT
    ]
    chalk = [
        lp for lp in lu.players
        if lp.player_id in ownership_by_player_id and ownership_by_player_id[lp.player_id].projected_ownership_pct >= CHALK_THRESHOLD_PCT
    ]

    parts = []
    if lu.stack_description:
        parts.append(lu.stack_description)
    if low_owned:
        parts.append(f"Includes {len(low_owned)} sub-{LOW_OWNED_THRESHOLD_PCT:.0f}%-owned leverage play(s).")
    if chalk:
        parts.append(f"Carries {len(chalk)} chalk play(s) (projected {CHALK_THRESHOLD_PCT:.0f}%+ owned) as a floor anchor.")
    parts.append(f"Projects {lu.projected_points} pts (ceiling {lu.ceiling}) at ${lu.salary_used:,} salary used, ${lu.salary_remaining:,} left on the table.")
    parts.append(f"Shares an average of {9 - uniqueness:.1f} players with other lineups in this portfolio ({uniqueness:.1f}-player uniqueness); average roster ownership {avg_ownership:.1f}%.")
    return " ".join(parts)
