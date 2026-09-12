"""Portfolio-level lineup generation with exposure and uniqueness control
— spec section 15. Generating N lineups is not "solve once, repeat N
times": each subsequent solve is constrained against the exposure caps and
overlap limits accumulated so far, so the whole portfolio is optimized
rather than N copies of the single best lineup.
"""
from __future__ import annotations

import copy
import dataclasses
import logging
import math

import numpy as np

from app.optimization.dk_rules import ContestRules
from app.optimization.optimizer import (
    InfeasibleLineupError,
    LineupResult,
    OptimizerPlayer,
    optimize_single_lineup,
)

logger = logging.getLogger("lineupopt.optimization")


@dataclasses.dataclass
class DiversificationSettings:
    max_player_exposure_pct: float = 40.0
    min_player_exposure_pct: float = 0.0
    max_lineup_overlap: int = 7
    min_unique_players: int = 2
    max_team_exposure_pct: float = 60.0
    max_qb_exposure_pct: float = 30.0


@dataclasses.dataclass
class PortfolioResult:
    lineups: list[LineupResult]
    warnings: list[str]


def generate_portfolio(
    players: list[OptimizerPlayer],
    rules: ContestRules,
    num_lineups: int,
    diversification: DiversificationSettings,
    forced_team_min_counts: dict[str, int] | None = None,
    forced_qb_stack_teams: list[str] | None = None,
    randomness_pct: float = 0.0,
    seed: int | None = None,
) -> PortfolioResult:
    """`forced_qb_stack_teams`, when given, is cycled through one team per
    lineup (index i uses `forced_qb_stack_teams[i % len(...)]`) so a GPP
    portfolio actually contains a spread of real QB+pass-catcher stacks
    (spec section 16) instead of leaving correlation to emerge from a
    linear objective, which under-selects it. Pass None/[] for stack-
    agnostic modes (e.g. cash, which optimizes floor/consistency instead).
    """
    rng = np.random.default_rng(seed)
    warnings: list[str] = []
    lineups: list[LineupResult] = []
    previous_player_sets: list[set[str]] = []

    exposure_count: dict[str, int] = {p.player_id: 0 for p in players}
    team_count: dict[str, int] = {}
    qb_count: dict[str, int] = {}

    max_allowed = max(1, math.ceil(diversification.max_player_exposure_pct / 100 * num_lineups))
    max_team_allowed = max(1, math.ceil(diversification.max_team_exposure_pct / 100 * num_lineups * rules.max_players_per_team))
    max_qb_allowed = max(1, math.ceil(diversification.max_qb_exposure_pct / 100 * num_lineups))

    effective_max_overlap = min(diversification.max_lineup_overlap, rules.roster_size - diversification.min_unique_players)

    for i in range(num_lineups):
        pool = copy.deepcopy(players)
        for p in pool:
            if exposure_count[p.player_id] >= max_allowed:
                p.excluded = True
            if p.position == "QB" and qb_count.get(p.player_id, 0) >= max_qb_allowed:
                p.excluded = True
            if team_count.get(p.team, 0) >= max_team_allowed:
                p.excluded = True
            if randomness_pct > 0:
                jitter = rng.uniform(-randomness_pct / 100, randomness_pct / 100)
                p.objective_value = round(p.objective_value * (1 + jitter), 4)

        stack_team = forced_qb_stack_teams[i % len(forced_qb_stack_teams)] if forced_qb_stack_teams else None
        try:
            lineup = optimize_single_lineup(
                pool,
                rules,
                forced_team_min_counts=forced_team_min_counts,
                forced_qb_stack_team=stack_team,
                exclude_lineups=previous_player_sets,
                max_overlap=effective_max_overlap,
            )
        except InfeasibleLineupError as exc:
            if stack_team:
                # That specific team's stack isn't buildable under the
                # current exposure/overlap constraints (e.g. its pass-
                # catchers are already exposure-capped) — fall back to an
                # unstacked solve for this slot rather than abandoning the
                # rest of the portfolio.
                try:
                    lineup = optimize_single_lineup(
                        pool, rules, forced_team_min_counts=forced_team_min_counts,
                        exclude_lineups=previous_player_sets, max_overlap=effective_max_overlap,
                    )
                    warnings.append(f"Lineup {i + 1}: {stack_team} stack infeasible, generated unstacked instead")
                except InfeasibleLineupError as exc2:
                    warnings.append(f"Stopped after {len(lineups)}/{num_lineups} lineups — solver infeasible: {exc2}")
                    break
            else:
                warnings.append(f"Stopped after {len(lineups)}/{num_lineups} lineups — solver infeasible: {exc}")
                break

        lineups.append(lineup)
        previous_player_sets.append(lineup.player_ids)
        for pid in lineup.player_ids:
            exposure_count[pid] = exposure_count.get(pid, 0) + 1
        for a in lineup.assignments:
            p = next(pp for pp in players if pp.player_id == a.player_id)
            team_count[p.team] = team_count.get(p.team, 0) + 1
            if p.position == "QB":
                qb_count[p.player_id] = qb_count.get(p.player_id, 0) + 1

    if len(lineups) < num_lineups and not warnings:
        warnings.append(f"Only generated {len(lineups)}/{num_lineups} lineups before exhausting feasible options")

    return PortfolioResult(lineups=lineups, warnings=warnings)


def compute_exposure_report(lineups: list[LineupResult], players: list[OptimizerPlayer]) -> dict[str, float]:
    if not lineups:
        return {}
    counts: dict[str, int] = {}
    for lu in lineups:
        for pid in lu.player_ids:
            counts[pid] = counts.get(pid, 0) + 1
    return {pid: round(count / len(lineups) * 100, 1) for pid, count in counts.items()}


def compute_uniqueness_score(lineup: LineupResult, other_lineups: list[LineupResult]) -> float:
    """Average number of DIFFERENT players vs the rest of the portfolio —
    higher is more unique."""
    if not other_lineups:
        return float(len(lineup.player_ids))
    diffs = [len(lineup.player_ids - other.player_ids) for other in other_lineups if other is not lineup]
    return round(sum(diffs) / len(diffs), 2) if diffs else float(len(lineup.player_ids))
