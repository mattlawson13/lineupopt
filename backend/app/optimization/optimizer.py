"""DraftKings-legal ILP lineup optimizer — spec section 13/14.

Roster/salary/FLEX rules are entirely config-driven (app/optimization/
dk_rules.py) rather than hardcoded, so a contest-rule change or a new
contest type never requires touching this solver.
"""
from __future__ import annotations

import dataclasses

import pulp

from app.optimization.dk_rules import ContestRules


@dataclasses.dataclass
class OptimizerPlayer:
    player_id: str
    position: str
    team: str
    game_id: str
    salary: int
    objective_value: float  # the score being maximized — caller decides what this represents (see portfolio/builder.py)
    locked: bool = False
    excluded: bool = False


@dataclasses.dataclass
class LineupPlayerAssignment:
    player_id: str
    slot: str
    salary: int
    objective_value: float


@dataclasses.dataclass
class LineupResult:
    assignments: list[LineupPlayerAssignment]
    salary_used: int
    objective_total: float

    @property
    def player_ids(self) -> set[str]:
        return {a.player_id for a in self.assignments}


class InfeasibleLineupError(RuntimeError):
    pass


def optimize_single_lineup(
    players: list[OptimizerPlayer],
    rules: ContestRules,
    forced_team_min_counts: dict[str, int] | None = None,
    forced_qb_stack_team: str | None = None,
    exclude_lineups: list[set[str]] | None = None,
    max_overlap: int | None = None,
    min_salary_used: int | None = None,
    time_limit_seconds: int = 20,
) -> LineupResult:
    """Solves one ILP lineup. `exclude_lineups` + `max_overlap` implement
    diversification: a candidate lineup may share at most `max_overlap`
    players with any lineup already in `exclude_lineups` (spec section 15).

    `forced_qb_stack_team`, when set, requires the QB slot be filled by a
    player from that team AND at least one WR/TE slot also be filled by a
    teammate — i.e. a genuine QB+pass-catcher stack (spec section 16),
    rather than leaving stacking to emerge (or not) from the objective
    alone, which tends to under-select correlation in a linear ILP.
    """
    pool = [p for p in players if not p.excluded]
    if not pool:
        raise InfeasibleLineupError("Player pool is empty after exclusions")

    prob = pulp.LpProblem("dk_lineup", pulp.LpMaximize)

    # x[player_id][slot_name] = 1 if player fills that slot
    x: dict[tuple[str, str], pulp.LpVariable] = {}
    for p in pool:
        for slot in rules.slots:
            if p.position in slot.eligible_positions:
                x[(p.player_id, slot.name)] = pulp.LpVariable(f"x_{p.player_id}_{slot.name}", cat="Binary")

    if not x:
        raise InfeasibleLineupError("No eligible (player, slot) pairs — check position eligibility")

    # Objective: sum of objective_value * slot's score_multiplier (captain/showdown support)
    slot_mult = {s.name: s.score_multiplier for s in rules.slots}
    prob += pulp.lpSum(
        var * next(p.objective_value for p in pool if p.player_id == pid) * slot_mult[slot]
        for (pid, slot), var in x.items()
    )

    # Each player used at most once across all slots
    by_player: dict[str, list[pulp.LpVariable]] = {}
    for (pid, slot), var in x.items():
        by_player.setdefault(pid, []).append(var)
    for pid, vars_ in by_player.items():
        prob += pulp.lpSum(vars_) <= 1

    # Each slot filled exactly `count` times
    by_slot: dict[str, list[pulp.LpVariable]] = {}
    for (pid, slot), var in x.items():
        by_slot.setdefault(slot, []).append(var)
    for slot in rules.slots:
        prob += pulp.lpSum(by_slot.get(slot.name, [])) == slot.count

    # Salary cap (respecting slot salary multipliers, e.g. Showdown CPT 1.5x)
    salary_by_pid = {p.player_id: p.salary for p in pool}
    slot_multiplier = {s.name: s.salary_multiplier for s in rules.slots}
    effective_salary = pulp.lpSum(
        var * salary_by_pid[pid] * slot_multiplier[slot] for (pid, slot), var in x.items()
    )
    prob += effective_salary <= rules.salary_cap
    if min_salary_used:
        # Same effective-salary expression as the cap above — a lineup's
        # reported salary_used already includes the CPT premium, so the
        # floor has to be measured the same way or it silently under-counts
        # a Showdown roster's real spend (confirmed live: this constraint
        # existed but was never wired up anywhere until it was, and its
        # un-multiplied version let Showdown lineups pass a "$44,000 floor"
        # while actually spending as little as $35,500 once CPT's 1.5x
        # premium was correctly applied to the reported total).
        prob += effective_salary >= min_salary_used

    # Locks
    for p in pool:
        if p.locked and p.player_id in by_player:
            prob += pulp.lpSum(by_player[p.player_id]) == 1

    # Forced QB stack: the QB slot must come from this team, and at least
    # one WR/TE (in any eligible slot) must also come from this team.
    if forced_qb_stack_team:
        qb_vars_from_team = [
            var for (pid, slot), var in x.items()
            if slot == "QB" and next(pp.team for pp in pool if pp.player_id == pid) == forced_qb_stack_team
        ]
        if qb_vars_from_team:
            prob += pulp.lpSum(qb_vars_from_team) == 1
            catcher_vars_from_team = [
                var for (pid, slot), var in x.items()
                if slot != "QB"
                and next(pp.team for pp in pool if pp.player_id == pid) == forced_qb_stack_team
                and next(pp.position for pp in pool if pp.player_id == pid) in ("WR", "TE")
            ]
            if catcher_vars_from_team:
                prob += pulp.lpSum(catcher_vars_from_team) >= 1

    # Team stacking: at least N players from a given team
    for team, min_count in (forced_team_min_counts or {}).items():
        team_vars = [var for (pid, slot), var in x.items() if next(pp.team for pp in pool if pp.player_id == pid) == team]
        if team_vars:
            prob += pulp.lpSum(team_vars) >= min_count

    # Max players per team
    teams = {p.team for p in pool}
    for team in teams:
        team_vars = [var for (pid, slot), var in x.items() if next(pp.team for pp in pool if pp.player_id == pid) == team]
        if team_vars:
            prob += pulp.lpSum(team_vars) <= rules.max_players_per_team

    # Min distinct games represented
    games = sorted({p.game_id for p in pool})
    if len(games) >= rules.min_games_represented:
        game_indicator = {g: pulp.LpVariable(f"game_used_{g}", cat="Binary") for g in games}
        for g in games:
            game_vars = [var for (pid, slot), var in x.items() if next(pp.game_id for pp in pool if pp.player_id == pid) == g]
            if game_vars:
                prob += pulp.lpSum(game_vars) >= game_indicator[g]
                prob += pulp.lpSum(game_vars) <= len(game_vars) * game_indicator[g]
        prob += pulp.lpSum(game_indicator.values()) >= rules.min_games_represented

    # Min distinct teams represented. This is a separate constraint from
    # min_games_represented above rather than redundant with it: in
    # Showdown, every player shares the same single game_id (one game on
    # the whole slate), so the games constraint is trivially satisfied
    # however the lineup is built and can never stop a lineup from being
    # drafted entirely off one team — that's exactly what this enforces
    # (DK requires both teams to be represented in Showdown).
    if len(teams) >= rules.min_teams_represented:
        team_indicator = {t: pulp.LpVariable(f"team_used_{t}", cat="Binary") for t in teams}
        for t in teams:
            t_vars = [var for (pid, slot), var in x.items() if next(pp.team for pp in pool if pp.player_id == pid) == t]
            if t_vars:
                prob += pulp.lpSum(t_vars) >= team_indicator[t]
                prob += pulp.lpSum(t_vars) <= len(t_vars) * team_indicator[t]
        prob += pulp.lpSum(team_indicator.values()) >= rules.min_teams_represented

    # Diversification: overlap cap against previously generated lineups
    if exclude_lineups and max_overlap is not None:
        for i, prev in enumerate(exclude_lineups):
            prev_vars = [var for (pid, slot), var in x.items() if pid in prev]
            if prev_vars:
                prob += pulp.lpSum(prev_vars) <= max_overlap

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_seconds)
    prob.solve(solver)

    if pulp.LpStatus[prob.status] != "Optimal":
        raise InfeasibleLineupError(f"Solver status: {pulp.LpStatus[prob.status]}")

    assignments = []
    salary_used = 0
    objective_total = 0.0
    for (pid, slot), var in x.items():
        if var.value() and var.value() > 0.5:
            p = next(pp for pp in pool if pp.player_id == pid)
            mult = next(s.salary_multiplier for s in rules.slots if s.name == slot)
            assignments.append(LineupPlayerAssignment(pid, slot, p.salary, p.objective_value))
            salary_used += int(p.salary * mult)
            objective_total += p.objective_value * slot_mult[slot]

    return LineupResult(assignments=assignments, salary_used=salary_used, objective_total=round(objective_total, 2))
