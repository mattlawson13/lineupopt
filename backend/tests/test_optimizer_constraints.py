import pytest

from app.optimization.dk_rules import get_contest_rules
from app.optimization.optimizer import OptimizerPlayer, optimize_single_lineup, InfeasibleLineupError


def make_pool():
    """A deliberately small, cheap-heavy pool so we can hand-verify the
    optimizer is forced to respect the salary cap and roster shape rather
    than just picking the highest-projection player at every slot.
    """
    players = []
    pid = 0
    # 2 games worth of teams so min_games_represented (2) is satisfiable.
    for game_idx, (team, opp) in enumerate([("BUF", "NYJ"), ("KC", "DEN")]):
        for pos, count in [("QB", 2), ("RB", 3), ("WR", 4), ("TE", 2), ("DST", 1)]:
            for i in range(count):
                pid += 1
                salary = 3000 + i * 1500 + game_idx * 200
                proj = salary / 1000 * 2.5 + i  # higher salary -> higher projection, deterministic
                players.append(OptimizerPlayer(f"p{pid}", pos, team, f"g{game_idx}", salary, round(proj, 2)))
    return players


def test_respects_salary_cap():
    rules = get_contest_rules("nfl", "classic")
    lineup = optimize_single_lineup(make_pool(), rules)
    assert lineup.salary_used <= rules.salary_cap


def test_respects_roster_shape():
    rules = get_contest_rules("nfl", "classic")
    lineup = optimize_single_lineup(make_pool(), rules)
    slot_counts = {}
    for a in lineup.assignments:
        slot_counts[a.slot] = slot_counts.get(a.slot, 0) + 1
    assert slot_counts == {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "DST": 1}
    assert len(lineup.assignments) == rules.roster_size


def test_locked_player_is_included():
    players = make_pool()
    players[5].locked = True  # a cheap RB, well below what the optimizer would pick unlocked
    rules = get_contest_rules("nfl", "classic")
    lineup = optimize_single_lineup(players, rules)
    assert players[5].player_id in lineup.player_ids


def test_excluded_player_never_appears():
    players = make_pool()
    best_dst = max([p for p in players if p.position == "DST"], key=lambda p: p.objective_value)
    best_dst.excluded = True
    rules = get_contest_rules("nfl", "classic")
    lineup = optimize_single_lineup(players, rules)
    assert best_dst.player_id not in lineup.player_ids


def test_max_players_per_team_enforced():
    rules = get_contest_rules("nfl", "classic")
    pool = make_pool()
    lineup = optimize_single_lineup(pool, rules)
    pool_by_id = {p.player_id: p for p in pool}
    team_counts = {}
    for a in lineup.assignments:
        team = pool_by_id[a.player_id].team
        team_counts[team] = team_counts.get(team, 0) + 1
    assert all(count <= rules.max_players_per_team for count in team_counts.values())


def test_infeasible_pool_raises():
    rules = get_contest_rules("nfl", "classic")
    tiny_pool = [OptimizerPlayer("p1", "QB", "BUF", "g1", 49000, 20.0)]
    with pytest.raises(InfeasibleLineupError):
        optimize_single_lineup(tiny_pool, rules)


def test_forced_qb_stack_team_includes_teammate():
    players = make_pool()
    rules = get_contest_rules("nfl", "classic")
    lineup = optimize_single_lineup(players, rules, forced_qb_stack_team="BUF")
    pool_by_id = {p.player_id: p for p in players}
    qb = next(a for a in lineup.assignments if a.slot == "QB")
    assert pool_by_id[qb.player_id].team == "BUF"
    catchers = [a for a in lineup.assignments if pool_by_id[a.player_id].position in ("WR", "TE") and pool_by_id[a.player_id].team == "BUF"]
    assert len(catchers) >= 1
