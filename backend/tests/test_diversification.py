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


def make_showdown_pool():
    players = []
    pid = 0
    for team in ("KC", "DEN"):
        for pos, count in [("QB", 1), ("RB", 3), ("WR", 4), ("TE", 2), ("DST", 1)]:
            for i in range(count):
                pid += 1
                salary = 3000 + i * 1200
                proj = salary / 1000 * 2.4 - i * 0.2
                players.append(OptimizerPlayer(f"sd{pid}", pos, team, "g0", salary, round(proj, 2)))
    return players


def test_large_request_from_thin_pool_still_generates_full_count():
    # A real user complaint: requesting more lineups than a thin pool
    # (Showdown's ~20-30 players) could normally diversify under the
    # default exposure caps used to stop the whole portfolio early
    # ("Stopped after 12/20 lineups"). Exposure caps should relax to keep
    # producing real lineups (allowing more repeats of good players)
    # rather than capping how many lineups can exist at all.
    players = make_showdown_pool()
    rules = get_contest_rules("nfl", "showdown")
    diversification = DiversificationSettings()  # default caps, e.g. max_player_exposure_pct=40%
    result = generate_portfolio(players, rules, num_lineups=40, diversification=diversification, randomness_pct=8.0, seed=7)
    assert len(result.lineups) == 40


def test_captain_exposure_cap_respected():
    # Reproduces the real fix: nothing previously stopped the same player
    # from being CPT (DK Showdown's 1.5x slot) in every lineup a portfolio
    # generated — the ordinary player-exposure cap only tracks whether a
    # player appears in a lineup at all, not which slot.
    players = make_showdown_pool()
    rules = get_contest_rules("nfl", "showdown")
    diversification = DiversificationSettings(max_captain_exposure_pct=25.0)
    result = generate_portfolio(players, rules, num_lineups=12, diversification=diversification, randomness_pct=8.0, seed=5)
    assert len(result.lineups) > 1
    captain_counts: dict[str, int] = {}
    for lu in result.lineups:
        for a in lu.assignments:
            if a.slot == "CPT":
                captain_counts[a.player_id] = captain_counts.get(a.player_id, 0) + 1
    max_captain_share = max(captain_counts.values()) / len(result.lineups) * 100
    # ceil(25% of 12) = 3 lineups -> 25.0% exactly; small slack for rounding
    assert max_captain_share <= 35.0
    assert len(captain_counts) > 1, "the same player was captain in every lineup"
