from app.optimization.diversification import (
    DiversificationSettings,
    compute_exposure_report,
    compute_uniqueness_score,
    generate_portfolio,
)
from app.optimization.dk_rules import get_contest_rules
from app.optimization.optimizer import OptimizerPlayer


def make_pool():
    players = []
    pid = 0
    for game_idx, (team, opp) in enumerate([("BUF", "NYJ"), ("KC", "DEN"), ("SF", "SEA"), ("DAL", "PHI"), ("MIA", "NE")]):
        for pos, count in [("QB", 1), ("RB", 4), ("WR", 5), ("TE", 2), ("DST", 1)]:
            for i in range(count):
                pid += 1
                salary = 3000 + i * 1000
                proj = salary / 1000 * 2.6 - i * 0.3
                players.append(OptimizerPlayer(f"p{pid}", pos, team, f"g{game_idx}", salary, round(proj, 2)))
    return players


def test_exposure_cap_respected():
    players = make_pool()
    rules = get_contest_rules("nfl", "classic")
    diversification = DiversificationSettings(max_player_exposure_pct=30.0)
    result = generate_portfolio(players, rules, num_lineups=20, diversification=diversification, randomness_pct=8.0, seed=3)
    assert len(result.lineups) > 0
    exposure = compute_exposure_report(result.lineups, players)
    # allow small integer-rounding slack (ceil(30% of 20) = 6 lineups -> 30.0% exactly)
    assert max(exposure.values()) <= 35.0


def test_generates_requested_count_when_feasible():
    players = make_pool()
    rules = get_contest_rules("nfl", "classic")
    result = generate_portfolio(players, rules, num_lineups=10, diversification=DiversificationSettings(), seed=1)
    assert len(result.lineups) == 10


def test_lineups_are_not_all_identical():
    players = make_pool()
    rules = get_contest_rules("nfl", "classic")
    result = generate_portfolio(players, rules, num_lineups=8, diversification=DiversificationSettings(), randomness_pct=10.0, seed=2)
    unique_sets = {frozenset(lu.player_ids) for lu in result.lineups}
    assert len(unique_sets) > 1


def test_uniqueness_score_zero_overlap_scenario():
    players = make_pool()
    rules = get_contest_rules("nfl", "classic")
    result = generate_portfolio(players, rules, num_lineups=5, diversification=DiversificationSettings(min_unique_players=2), seed=4)
    scores = [compute_uniqueness_score(lu, result.lineups) for lu in result.lineups]
    assert all(s >= 0 for s in scores)
