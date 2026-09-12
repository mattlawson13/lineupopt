from app.models.enums import StackType
from app.optimization.optimizer import LineupPlayerAssignment, OptimizerPlayer
from app.optimization.stacking import classify_stack


def _player(pid, pos, team, game_id="g1"):
    return OptimizerPlayer(pid, pos, team, game_id, salary=5000, objective_value=10.0)


def test_naked_qb():
    pool = {p.player_id: p for p in [_player("qb", "QB", "BUF"), _player("wr", "WR", "KC")]}
    assignments = [LineupPlayerAssignment("qb", "QB", 5000, 10.0), LineupPlayerAssignment("wr", "WR", 5000, 10.0)]
    result = classify_stack(assignments, pool)
    assert result.stack_type == StackType.NAKED_QB


def test_standard_stack():
    pool = {p.player_id: p for p in [_player("qb", "QB", "BUF"), _player("wr", "WR", "BUF")]}
    assignments = [LineupPlayerAssignment("qb", "QB", 5000, 10.0), LineupPlayerAssignment("wr", "WR", 5000, 10.0)]
    result = classify_stack(assignments, pool)
    assert result.stack_type == StackType.STANDARD


def test_double_stack():
    pool = {p.player_id: p for p in [_player("qb", "QB", "BUF"), _player("wr", "WR", "BUF"), _player("te", "TE", "BUF")]}
    assignments = [
        LineupPlayerAssignment("qb", "QB", 5000, 10.0),
        LineupPlayerAssignment("wr", "WR", 5000, 10.0),
        LineupPlayerAssignment("te", "TE", 5000, 10.0),
    ]
    result = classify_stack(assignments, pool)
    assert result.stack_type == StackType.DOUBLE_STACK


def test_bring_back():
    pool = {p.player_id: p for p in [
        _player("qb", "QB", "BUF"), _player("wr", "WR", "BUF"), _player("opp_wr", "WR", "NYJ"),
    ]}
    assignments = [
        LineupPlayerAssignment("qb", "QB", 5000, 10.0),
        LineupPlayerAssignment("wr", "WR", 5000, 10.0),
        LineupPlayerAssignment("opp_wr", "WR", 5000, 10.0),
    ]
    result = classify_stack(assignments, pool)
    assert result.stack_type == StackType.BRING_BACK


def test_run_back():
    pool = {p.player_id: p for p in [
        _player("qb", "QB", "BUF"), _player("wr", "WR", "BUF"), _player("opp_rb", "RB", "NYJ"),
    ]}
    assignments = [
        LineupPlayerAssignment("qb", "QB", 5000, 10.0),
        LineupPlayerAssignment("wr", "WR", 5000, 10.0),
        LineupPlayerAssignment("opp_rb", "RB", 5000, 10.0),
    ]
    result = classify_stack(assignments, pool)
    assert result.stack_type == StackType.RUN_BACK


def test_game_stack():
    pool = {p.player_id: p for p in [
        _player("qb", "QB", "BUF"), _player("wr1", "WR", "BUF"), _player("wr2", "WR", "BUF"), _player("opp_wr", "WR", "NYJ"),
    ]}
    assignments = [
        LineupPlayerAssignment("qb", "QB", 5000, 10.0),
        LineupPlayerAssignment("wr1", "WR", 5000, 10.0),
        LineupPlayerAssignment("wr2", "WR", 5000, 10.0),
        LineupPlayerAssignment("opp_wr", "WR", 5000, 10.0),
    ]
    result = classify_stack(assignments, pool)
    assert result.stack_type == StackType.GAME_STACK


def test_no_qb_lineup_returns_none_type():
    pool = {p.player_id: p for p in [_player("rb", "RB", "BUF"), _player("wr", "WR", "KC")]}
    assignments = [LineupPlayerAssignment("rb", "RB", 5000, 10.0), LineupPlayerAssignment("wr", "WR", 5000, 10.0)]
    result = classify_stack(assignments, pool)
    assert result.stack_type is None
