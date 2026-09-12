from app.projections.nfl.dk_points import compute_dk_points


def test_qb_scoring_known_line(known_qb_stat_line):
    pts = compute_dk_points(known_qb_stat_line)
    assert pts == 32.5


def test_rb_scoring_known_line(known_rb_stat_line):
    pts = compute_dk_points(known_rb_stat_line)
    assert pts == 25.5


def test_passing_300_yard_bonus():
    base = compute_dk_points({"pass_yards": 299})
    with_bonus = compute_dk_points({"pass_yards": 300})
    # 1 extra yard (0.04) plus the 3pt bonus for crossing the 300 threshold
    assert round(with_bonus - base, 2) == 3.04


def test_dst_points_allowed_tiers():
    shutout = compute_dk_points({"points_allowed": 0})
    one_score_game = compute_dk_points({"points_allowed": 20})  # falls in the 14-20 tier -> +1.0
    field_goal_range = compute_dk_points({"points_allowed": 24})  # falls in the 21-27 tier -> +0.0
    blowout_loss = compute_dk_points({"points_allowed": 40})
    assert shutout == 10.0
    assert one_score_game == 1.0
    assert field_goal_range == 0.0
    assert blowout_loss == -4.0


def test_kicker_scoring():
    pts = compute_dk_points({"fg_0_39": 1, "fg_40_49": 1, "fg_50_plus": 1, "xp_made": 3})
    assert pts == 3.0 + 4.0 + 5.0 + 3 * 1.0


def test_fumble_lost_penalty():
    assert compute_dk_points({"fumbles_lost": 1}) == -1.0


def test_empty_stat_line_is_zero():
    assert compute_dk_points({}) == 0.0
