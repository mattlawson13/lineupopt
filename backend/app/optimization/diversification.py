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


def _solve_one_lineup(
    pool, rules, forced_team_min_counts, stack_team, previous_player_sets,
    effective_max_overlap, min_salary_used, lineup_index: int, warnings: list[str],
) -> LineupResult:
    """Tries the strictest constraint combination first (forced stack +
    salary floor), then relaxes one constraint at a time rather than
    aborting the whole portfolio the moment either one doesn't fit this
    lineup's shrunken, exposure-capped pool — a later lineup in a big
    batch has fewer eligible players left than the first one did. Every
    relaxation is reported, never silent.
    """
    attempts: list[dict] = []
    if stack_team:
        attempts.append({"forced_qb_stack_team": stack_team, "min_salary_used": min_salary_used})
    attempts.append({"forced_qb_stack_team": None, "min_salary_used": min_salary_used})
    if min_salary_used:
        attempts.append({"forced_qb_stack_team": None, "min_salary_used": None})

    last_exc: InfeasibleLineupError | None = None
    for kwargs in attempts:
        try:
            lineup = optimize_single_lineup(
                pool, rules, forced_team_min_counts=forced_team_min_counts,
                exclude_lineups=previous_player_sets, max_overlap=effective_max_overlap, **kwargs,
            )
        except InfeasibleLineupError as exc:
            last_exc = exc
            continue
        if stack_team and kwargs["forced_qb_stack_team"] is None:
            warnings.append(f"Lineup {lineup_index + 1}: {stack_team} stack infeasible, generated unstacked instead")
        elif min_salary_used and kwargs["min_salary_used"] is None:
            warnings.append(f"Lineup {lineup_index + 1}: relaxed the minimum-salary floor to stay feasible")
        return lineup
    raise last_exc  # every attempt failed — let the caller decide whether to stop the portfolio


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
    seed_exclude_lineups: list[set[str]] | None = None,
    min_salary_used: int | None = None,
) -> PortfolioResult:
    """`forced_qb_stack_teams`, when given, is cycled through one team per
    lineup (index i uses `forced_qb_stack_teams[i % len(...)]`) so a GPP
    portfolio actually contains a spread of real QB+pass-catcher stacks
    (spec section 16) instead of leaving correlation to emerge from a
    linear objective, which under-selects it. Pass None/[] for stack-
    agnostic modes (e.g. cash, which optimizes floor/consistency instead).

    `seed_exclude_lineups`: player-id sets for lineups that already exist
    (e.g. from an earlier build/generate call) — seeds the same overlap
    constraint this function already enforces *within* one portfolio, so
    a fresh batch stays genuinely different from lineups you already have
    from the very first one generated, not just from each other.

    `min_salary_used`: the ILP already supported this constraint
    (optimizer.py) but nothing ever passed it, so nothing stopped a
    lineup from leaving a large chunk of the cap completely unspent —
    confirmed live on a real Showdown build: lineups leaving 28-41% of a
    $50,000 cap unused, real lost expected points, not a deliberate
    "stars and scrubs" build (which leaves at most a few hundred dollars,
    not $15-20K). Relaxed automatically (see _solve_one_lineup) rather
    than aborting the portfolio if a later, exposure-thinned lineup can't
    meet it.
    """
    rng = np.random.default_rng(seed)
    warnings: list[str] = []
    lineups: list[LineupResult] = []
    previous_player_sets: list[set[str]] = list(seed_exclude_lineups or [])

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
            lineup = _solve_one_lineup(
                pool, rules, forced_team_min_counts, stack_team, previous_player_sets,
                effective_max_overlap, min_salary_used, i, warnings,
            )
        except InfeasibleLineupError as exc:
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
