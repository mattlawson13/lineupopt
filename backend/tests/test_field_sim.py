import numpy as np

from app.optimization.dk_rules import get_contest_rules
from app.optimization.optimizer import LineupPlayerAssignment, LineupResult, OptimizerPlayer, optimize_single_lineup
from app.simulation.field_sim import FieldSimSettings, simulate_field, synthesize_field


def make_pool():
    players = []
    pid = 0
    for game_idx, (team, opp) in enumerate([("BUF", "NYJ"), ("KC", "DEN")]):
        for pos, count in [("QB", 1), ("RB", 3), ("WR", 4), ("TE", 2), ("DST", 1)]:
            for i in range(count):
                pid += 1
                salary = 3000 + i * 1500 + game_idx * 200
                proj = salary / 1000 * 2.5 + i
                players.append(OptimizerPlayer(f"p{pid}", pos, team, f"g{game_idx}", salary, round(proj, 2)))
    return players


def make_draws(pool, num_sims=500, seed=0):
    """Deterministic per-player draws centered on objective_value, higher
    variance for cheaper/lower-projection players — mimics real Monte
    Carlo output shape closely enough for these tests."""
    rng = np.random.default_rng(seed)
    return {p.player_id: rng.normal(p.objective_value, max(p.objective_value * 0.3, 1.0), size=num_sims) for p in pool}


def make_ownership(pool):
    # Chalk toward higher-projection players, like a real ownership model.
    return {p.player_id: min(40.0, max(1.0, p.objective_value)) for p in pool}


def test_synthesize_field_produces_legal_lineups():
    pool = make_pool()
    rules = get_contest_rules("nfl", "classic")
    settings = FieldSimSettings(num_field_lineups=25)
    rng = np.random.default_rng(1)
    field = synthesize_field(pool, make_ownership(pool), rules, 25, settings, rng)
    assert len(field) > 0
    pool_by_id = {p.player_id: p for p in pool}
    for lu in field:
        assert len(lu) == rules.roster_size
        picked_ids = [pid for _, pid in lu]
        assert len(set(picked_ids)) == rules.roster_size  # no duplicate players
        salary = sum(pool_by_id[pid].salary for pid in picked_ids)
        assert salary <= rules.salary_cap


def test_stronger_lineup_beats_weaker_lineup_more_often():
    pool = make_pool()
    rules = get_contest_rules("nfl", "classic")
    draws = make_draws(pool)
    ownership = make_ownership(pool)

    strong = optimize_single_lineup(pool, rules)  # the real optimizer's best lineup
    weak_players = sorted(pool, key=lambda p: p.objective_value)[: rules.roster_size]
    weak = LineupResult(
        assignments=[LineupPlayerAssignment(p.player_id, "FLEX", p.salary, p.objective_value) for p in weak_players],
        salary_used=sum(p.salary for p in weak_players), objective_total=sum(p.objective_value for p in weak_players),
    )

    results = simulate_field(
        [strong, weak], pool, ownership, rules, draws,
        settings=FieldSimSettings(num_field_lineups=200, seed=5),
    )
    strong_result, weak_result = results
    assert strong_result.mean_percentile > weak_result.mean_percentile
    assert strong_result.cash_pct >= weak_result.cash_pct
    for r in results:
        assert 0.0 <= r.win_pct <= 100.0
        assert 0.0 <= r.cash_pct <= 100.0
        assert r.top1pct_pct <= r.top5pct_pct + 1e-9
        assert r.top5pct_pct <= r.cash_pct + 1e-9 or r.cash_pct >= 20.0 - 1e-6  # cash line ~ top 20% by default


def test_roi_uses_real_contest_numbers_when_given():
    pool = make_pool()
    rules = get_contest_rules("nfl", "classic")
    draws = make_draws(pool)
    ownership = make_ownership(pool)
    lineup = optimize_single_lineup(pool, rules)

    results = simulate_field(
        [lineup], pool, ownership, rules, draws,
        settings=FieldSimSettings(num_field_lineups=150, seed=2),
        total_prizes=100_000.0, entry_fee=20.0, max_entries=5000,
    )
    assert results[0].payout_basis == "contest_real"
    assert results[0].roi_pct is not None

    generic = simulate_field(
        [lineup], pool, ownership, rules, draws,
        settings=FieldSimSettings(num_field_lineups=150, seed=2),
    )
    assert generic[0].payout_basis == "approximate_generic"


def test_no_draws_returns_unavailable_not_a_crash():
    pool = make_pool()
    rules = get_contest_rules("nfl", "classic")
    lineup = optimize_single_lineup(pool, rules)
    results = simulate_field([lineup], pool, make_ownership(pool), rules, {}, settings=FieldSimSettings())
    assert results[0].payout_basis == "unavailable"
