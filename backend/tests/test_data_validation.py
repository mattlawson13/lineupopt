from app.data_sources.draftkings import DraftKingsPlayerRow, DraftKingsSlate
from app.ingestion.validation import has_blocking_errors, validate_dk_slate, validate_projection


def _row(**overrides):
    defaults = dict(
        dk_player_id="1", dk_draftable_id="1", display_name="Test Player", position="WR",
        salary=6000, team_abbreviation="BUF", opponent_abbreviation="NYJ",
        game_description="NYJ @ BUF", game_start_time_utc=None, roster_status="active",
    )
    defaults.update(overrides)
    return DraftKingsPlayerRow(**defaults)


def test_valid_slate_has_no_errors():
    slate = DraftKingsSlate("g1", [_row(dk_player_id="1"), _row(dk_player_id="2", position="RB")])
    issues = validate_dk_slate(slate)
    assert not has_blocking_errors(issues)


def test_missing_salary_flagged_as_error():
    slate = DraftKingsSlate("g1", [_row(salary=0)])
    issues = validate_dk_slate(slate)
    assert has_blocking_errors(issues)
    assert any(i.code == "missing_salary" for i in issues)


def test_invalid_position_flagged():
    slate = DraftKingsSlate("g1", [_row(position="XYZ")])
    issues = validate_dk_slate(slate)
    assert any(i.code == "invalid_position" and i.severity == "error" for i in issues)


def test_missing_team_flagged():
    slate = DraftKingsSlate("g1", [_row(team_abbreviation="")])
    issues = validate_dk_slate(slate)
    assert any(i.code == "missing_team" for i in issues)


def test_duplicate_player_flagged_as_warning_not_error():
    slate = DraftKingsSlate("g1", [_row(dk_player_id="1"), _row(dk_player_id="1")])
    issues = validate_dk_slate(slate)
    dup = [i for i in issues if i.code == "duplicate_player"]
    assert dup and dup[0].severity == "warning"


def test_empty_slate_flagged():
    slate = DraftKingsSlate("g1", [])
    issues = validate_dk_slate(slate)
    assert has_blocking_errors(issues)


def test_negative_projection_is_error():
    issues = validate_projection("p1", "Test", "WR", 6000, -3.0)
    assert has_blocking_errors(issues)


def test_projection_outlier_is_warning_not_error():
    issues = validate_projection("p1", "Test", "WR", 6000, 80.0)
    assert not has_blocking_errors(issues)
    assert any(i.code == "projection_outlier" for i in issues)


def test_projection_without_salary_is_error():
    issues = validate_projection("p1", "Test", "WR", 0, 12.0)
    assert has_blocking_errors(issues)
