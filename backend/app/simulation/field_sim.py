"""Contest-field simulation — scores our own lineups against a modeled
field of opponent entries, using the SAME per-simulation player-score
draws the Monte Carlo engine already produced (monte_carlo.py's
SlateSimulationResult.player_draws). Because every lineup — ours and the
field's — is scored against the identical drawn "worlds," correlation
across a simulated world is automatically shared: if a QB+WR stack booms
together in simulation #42, both our lineup and any field lineup that
happens to also roster them get that same correlated boost in #42.

This is the real mechanism serious DFS tools (Stokastic's "Sims" product,
SaberSim, etc.) are publicly known to be built around — well-documented
DFS theory, not anyone's proprietary internals: simulate every player's
outcome jointly, build a field of opponent lineups weighted by projected
ownership, score everyone against each simulated world, and read off real
contest-equity metrics (win%, top1%, cash%, ROI against a payout curve)
instead of the crude `prob_top5pct% - ownership%` linear proxy that used
to be the only leverage signal in the objective.

Two honest limitations, documented rather than hidden (same policy as
optimization/contest_calibration.py and backtesting/engine.py):

1. The "field" is SYNTHETIC — built from OUR OWN ownership model and a
   greedy salary-feasible construction, not observed from a real
   contest's actual entries (DraftKings doesn't expose those). It
   approximates a realistic field's roster-construction tendencies, not
   the field.
2. The payout curve is a top-heavy shape calibrated to a SPECIFIC
   contest's real total_prizes/max_entries/entry_fee when known (DK's own
   lobby listing exposes these — data_sources/draftkings.py's
   get_nfl_contests), or a generic assumed shape (config's
   assumed_rake_pct/cash_line_pct) otherwise. Either way the exact
   rank-by-rank payout table DK actually pays is not exposed by any known
   endpoint, so ROI numbers are a calibrated estimate, not the literal
   dollar amount a real entry would win.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from app.optimization.dk_rules import ContestRules
from app.optimization.optimizer import LineupResult


@dataclasses.dataclass
class FieldSimSettings:
    num_field_lineups: int = 1000
    max_sims_used: int = 2000
    cash_line_pct: float = 0.20
    payout_shape_alpha: float = 2.5
    assumed_rake_pct: float = 0.15
    min_ownership_weight_pct: float = 0.5
    # Used ONLY as the assumed real contest size when no specific
    # dk_contest_id's real max_entries is available — deliberately NOT the
    # same number as num_field_lineups (the synthetic sample count).
    # Conflating those two was a real bug: reporting win%/cash% against a
    # 1,000-entry field when the real thing is a 50,000-entry GPP produces
    # absurdly optimistic numbers (measured live: 59% win rate, 99.8%
    # cash on a real large_field_gpp build) that have nothing to do with
    # the actual contest size. 10,000 is a generic mid-large-GPP
    # assumption, not a guess at any specific contest.
    default_max_entries: int = 10_000
    seed: int | None = None


@dataclasses.dataclass
class FieldSimResult:
    win_pct: float
    top1pct_pct: float
    top5pct_pct: float
    cash_pct: float
    mean_percentile: float
    roi_pct: float | None  # None when no entry fee is known to compute ROI against
    payout_basis: str  # "contest_real" | "approximate_generic"
    field_size: int


def _expand_slots(rules: ContestRules) -> list[tuple[str, list[str], float]]:
    """One entry per roster spot (not per slot type) — e.g. RB count=2
    becomes two separate ("RB", [...], 1.0) entries — so field-lineup
    synthesis fills them independently."""
    out = []
    for slot in rules.slots:
        for _ in range(slot.count):
            out.append((slot.name, slot.eligible_positions, slot.score_multiplier))
    return out


def _cheapest_salary_lookup(pool: list, slot_instances: list[tuple[str, list[str], float]]) -> dict[tuple, int]:
    """Cheapest salary per DISTINCT eligible-positions set appearing in
    slot_instances (e.g. classic has ~6: QB, RB, WR, TE, DST, and FLEX's
    [RB,WR,TE]), computed ONCE from the full pool rather than rebuilt from
    a used-players-filtered scan on every slot of every field lineup — the
    naive version re-scanned the whole remaining pool for every remaining
    slot of every slot of every lineup (O(lineups * slots^2 * pool_size)),
    which measured well over two minutes for a single realistic Classic-
    scale pool (~300 players) and never finished for a full-size one
    (~750). This is a deliberate, cheap approximation: ignoring which
    specific players are already used when computing "cheapest for this
    position" essentially never changes the minimum at real NFL DFS pool
    sizes, and the rare case where it's wrong is absorbed by
    synthesize_field's cheapest-available fallback.
    """
    lookup: dict[tuple, int] = {}
    for _, elig, _ in slot_instances:
        key = tuple(sorted(elig))
        if key not in lookup:
            salaries = [p.salary for p in pool if p.position in elig]
            lookup[key] = min(salaries) if salaries else 0
    return lookup


def _candidate_base_by_slot(pool: list, slot_instances: list[tuple[str, list[str], float]]) -> dict[tuple, list]:
    """Pre-filters the pool to just the eligible players for each DISTINCT
    eligible-positions set, once, so each field lineup's per-slot pick
    only has to scan its own (usually much smaller than the full pool)
    position group instead of the entire pool every time."""
    base: dict[tuple, list] = {}
    for _, elig, _ in slot_instances:
        key = tuple(sorted(elig))
        if key not in base:
            base[key] = [p for p in pool if p.position in elig]
    return base


def synthesize_field(
    pool: list,
    ownership_by_pid: dict[str, float],
    rules: ContestRules,
    num_lineups: int,
    settings: FieldSimSettings,
    rng: np.random.Generator,
) -> list[list[tuple[str, str]]]:
    """Greedy, ownership-weighted random roster construction — NOT a full
    ILP solve per field lineup (would be far too slow for a field of
    hundreds/thousands). Approximates how a real field distributes across
    players: popular (chalk) players get rostered far more often, exactly
    like real human/lobby-tool-built lineups skew toward consensus plays,
    while still respecting DK's hard salary cap, position eligibility,
    max-players-per-team, and min-teams-represented rules.

    Returns a list of [(slot_name, player_id), ...] per field lineup — a
    list, not a dict, because a slot name like "RB" or "WR" legitimately
    repeats (DK's per-slot count > 1); keying by slot name would silently
    drop every repeat but the last. A lineup that can't be completed under
    the retry budget falls back to the cheapest eligible player for its
    remaining slots — guaranteed feasible since the real optimizer already
    built legal lineups from this same pool, so a min-cost roster is
    always obtainable.
    """
    slot_instances = _expand_slots(rules)
    floor_weight = settings.min_ownership_weight_pct
    cheapest_lookup = _cheapest_salary_lookup(pool, slot_instances)
    candidate_base = _candidate_base_by_slot(pool, slot_instances)
    # Precomputed once: for each slot index, the summed cheapest-salary
    # reservation needed for every slot AFTER it (replaces an O(slots) sum
    # done from scratch at every slot of every lineup with an O(1) lookup).
    remaining_cost_by_index = [0.0] * (len(slot_instances) + 1)
    for i in range(len(slot_instances) - 1, -1, -1):
        _, elig, mult = slot_instances[i]
        remaining_cost_by_index[i] = remaining_cost_by_index[i + 1] + cheapest_lookup[tuple(sorted(elig))] * mult

    lineups: list[list[tuple[str, str]]] = []
    for _ in range(num_lineups):
        chosen: list[tuple[str, str]] = []
        used_ids: set[str] = set()
        team_counts: dict[str, int] = {}
        remaining_salary = rules.salary_cap

        for i, (slot_name, eligible_positions, salary_mult) in enumerate(slot_instances):
            base = candidate_base[tuple(sorted(eligible_positions))]
            candidates = [
                p for p in base
                if p.player_id not in used_ids
                and team_counts.get(p.team, 0) < rules.max_players_per_team
            ]
            if not candidates:
                continue

            # Salary feasibility: this pick's cost must leave enough room
            # for at least the cheapest legal filler in every remaining slot.
            min_remaining_cost = remaining_cost_by_index[i + 1]
            feasible = [
                p for p in candidates
                if p.salary * salary_mult + min_remaining_cost <= remaining_salary
            ]
            if not feasible:
                feasible = sorted(candidates, key=lambda p: p.salary)[:1]  # cheapest available, may bust later slots' feasibility (rare, retried lineups still dominate)
            if not feasible:
                continue

            weights = np.array([
                max(ownership_by_pid.get(p.player_id, floor_weight), floor_weight) for p in feasible
            ])
            weights = weights / weights.sum()
            pick = feasible[rng.choice(len(feasible), p=weights)]

            chosen.append((slot_name, pick.player_id))
            used_ids.add(pick.player_id)
            team_counts[pick.team] = team_counts.get(pick.team, 0) + 1
            remaining_salary -= int(pick.salary * salary_mult)

        # Both-teams requirement (Showdown): if the greedy fill landed on
        # one team only, force-swap the last pick for a cheap player from
        # an unrepresented team, when one exists.
        if rules.min_teams_represented > 1 and len(team_counts) < rules.min_teams_represented and chosen:
            missing_team_players = [p for p in pool if p.team not in team_counts and p.player_id not in used_ids]
            if missing_team_players:
                swap_slot, _ = chosen[-1]
                replacement = min(missing_team_players, key=lambda p: p.salary)
                chosen[-1] = (swap_slot, replacement.player_id)

        if len(chosen) == len(slot_instances):
            lineups.append(chosen)

    return lineups


def _score_lineups(
    lineups: list[list[tuple[str, str]]],
    slot_score_mult: dict[str, float],
    player_draws: dict[str, np.ndarray],
    n_sims: int,
) -> np.ndarray:
    """(len(lineups), n_sims) matrix of total lineup scores per simulated
    world, built by summing each rostered player's draw row times their
    slot's score multiplier (e.g. Showdown CPT 1.5x)."""
    scores = np.zeros((len(lineups), n_sims))
    for i, lu in enumerate(lineups):
        for slot_name, pid in lu:
            draws = player_draws.get(pid)
            if draws is None:
                continue
            scores[i] += draws[:n_sims] * slot_score_mult.get(slot_name, 1.0)
    return scores


def _score_candidate(
    lineup: LineupResult,
    slot_score_mult: dict[str, float],
    player_draws: dict[str, np.ndarray],
    n_sims: int,
) -> np.ndarray:
    total = np.zeros(n_sims)
    for a in lineup.assignments:
        draws = player_draws.get(a.player_id)
        if draws is None:
            continue
        total += draws[:n_sims] * slot_score_mult.get(a.slot, 1.0)
    return total


def _payout_table(
    field_size_real: int,
    cash_line_pct: float,
    alpha: float,
    total_prizes: float,
) -> np.ndarray:
    """payout[r] for r in [0, cash_count) = $ payout for finishing rank
    r+1 (1st place at index 0), summing to total_prizes, shaped by a
    power-law top-heaviness (`alpha`) — a well-known, publicly documented
    approximation of large-field GPP payout curves (a small top-end spike,
    a long min-cash tail), not any specific contest's exact real table.
    """
    cash_count = max(1, round(field_size_real * cash_line_pct))
    ranks = np.arange(1, cash_count + 1)
    raw_weights = (cash_count - ranks + 1.0) ** alpha
    return total_prizes * raw_weights / raw_weights.sum()


def simulate_field(
    candidate_lineups: list[LineupResult],
    pool: list,
    ownership_by_player_id: dict[str, float],
    rules: ContestRules,
    player_draws: dict[str, np.ndarray],
    settings: FieldSimSettings | None = None,
    total_prizes: float | None = None,
    entry_fee: float | None = None,
    max_entries: int | None = None,
) -> list[FieldSimResult]:
    """Returns one FieldSimResult per candidate lineup, in input order.

    `total_prizes`/`entry_fee`/`max_entries`: pass the SPECIFIC contest's
    real values (from DraftKings' own lobby listing) to calibrate the
    payout curve to real money — otherwise a generic assumed shape
    (settings.assumed_rake_pct/cash_line_pct against the synthetic field
    size) is used and `payout_basis` is reported as "approximate_generic"
    so callers never mistake it for a real number.
    """
    settings = settings or FieldSimSettings()
    rng = np.random.default_rng(settings.seed)

    any_draws = next(iter(player_draws.values()), None)
    if any_draws is None or not candidate_lineups:
        return [
            FieldSimResult(0.0, 0.0, 0.0, 0.0, 0.0, None, "unavailable", 0)
            for _ in candidate_lineups
        ]
    n_sims = min(len(any_draws), settings.max_sims_used)

    field_lineups = synthesize_field(pool, ownership_by_player_id, rules, settings.num_field_lineups, settings, rng)
    slot_score_mult = {s.name: s.score_multiplier for s in rules.slots}

    field_scores = _score_lineups(field_lineups, slot_score_mult, player_draws, n_sims)  # (field_size, n_sims)
    field_size = field_scores.shape[0]
    if field_size == 0:
        return [
            FieldSimResult(0.0, 0.0, 0.0, 0.0, 0.0, None, "unavailable", 0)
            for _ in candidate_lineups
        ]

    real_field_size = max_entries or settings.default_max_entries
    if total_prizes and entry_fee and max_entries:
        payout_basis = "contest_real"
    else:
        payout_basis = "approximate_generic"
        entry_fee = entry_fee or 20.0  # nominal — only used to express ROI as a %, cancels out in the ratio
        total_prizes = total_prizes or entry_fee * real_field_size * (1 - settings.assumed_rake_pct)

    payouts = _payout_table(real_field_size, settings.cash_line_pct, settings.payout_shape_alpha, total_prizes)
    cash_count = len(payouts)

    results: list[FieldSimResult] = []
    for lineup in candidate_lineups:
        cand_scores = _score_candidate(lineup, slot_score_mult, player_draws, n_sims)  # (n_sims,)
        beaten = (field_scores < cand_scores[None, :]).sum(axis=0)  # count of field entries this candidate outscores, per sim
        percentile = beaten / field_size  # 1.0 = beat the entire synthetic field

        est_rank = np.clip(np.round((1 - percentile) * real_field_size).astype(int), 1, real_field_size)
        payout_per_sim = np.where(est_rank <= cash_count, payouts[np.clip(est_rank, 1, cash_count) - 1], 0.0)

        win_pct = float(np.mean(est_rank == 1) * 100)
        top1pct_pct = float(np.mean(percentile >= 0.99) * 100)
        top5pct_pct = float(np.mean(percentile >= 0.95) * 100)
        cash_pct = float(np.mean(est_rank <= cash_count) * 100)
        mean_percentile = float(np.mean(percentile) * 100)
        roi_pct = float((np.mean(payout_per_sim) / entry_fee - 1) * 100) if entry_fee else None

        results.append(FieldSimResult(
            win_pct=round(win_pct, 3), top1pct_pct=round(top1pct_pct, 2), top5pct_pct=round(top5pct_pct, 2),
            cash_pct=round(cash_pct, 2), mean_percentile=round(mean_percentile, 2),
            roi_pct=round(roi_pct, 1) if roi_pct is not None else None,
            payout_basis=payout_basis, field_size=field_size,
        ))
    return results
