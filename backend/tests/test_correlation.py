from app.correlations.engine import (
    CorrelationPlayer,
    build_correlation_matrix,
    classify_relationship,
    compute_empirical_correlation,
)


def test_classify_qb_own_wr():
    qb = CorrelationPlayer("qb1", "QB", "BUF", "NYJ")
    wr = CorrelationPlayer("wr1", "WR", "BUF", "NYJ")
    assert classify_relationship(qb, wr) == "qb_own_wr"


def test_classify_qb_opp_wr_bring_back():
    qb = CorrelationPlayer("qb1", "QB", "BUF", "NYJ")
    opp_wr = CorrelationPlayer("wr2", "WR", "NYJ", "BUF")
    assert classify_relationship(qb, opp_wr) == "qb_opp_wr"


def test_unrelated_players_return_none():
    a = CorrelationPlayer("a", "WR", "BUF", "NYJ")
    b = CorrelationPlayer("b", "WR", "KC", "DEN")
    assert classify_relationship(a, b) is None


def test_empirical_correlation_perfect_positive():
    series_a = [10, 12, 14, 16, 18, 20, 22, 24]
    series_b = [5, 6, 7, 8, 9, 10, 11, 12]
    corr, n = compute_empirical_correlation(series_a, series_b)
    assert corr > 0.99
    assert n == 8


def test_empirical_correlation_needs_min_samples():
    corr, n = compute_empirical_correlation([1, 2], [3, 4])
    assert corr == 0.0  # below the 3-sample floor, returns 0 rather than an unstable estimate


def test_build_matrix_falls_back_to_prior_without_history():
    players = [
        CorrelationPlayer("qb1", "QB", "BUF", "NYJ"),
        CorrelationPlayer("wr1", "WR", "BUF", "NYJ"),
    ]
    entries = build_correlation_matrix(players)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.relationship_type == "qb_own_wr"
    assert entry.is_empirical is False
    assert entry.correlation > 0  # QB/WR prior is positive


def test_conditional_multiplier_boosts_high_total_games():
    players = [
        CorrelationPlayer("qb1", "QB", "BUF", "NYJ"),
        CorrelationPlayer("wr1", "WR", "BUF", "NYJ"),
    ]
    low_total = build_correlation_matrix(players, game_context={"BUF": {"total": 38.0, "spread": 0.0}})
    high_total = build_correlation_matrix(players, game_context={"BUF": {"total": 54.0, "spread": 0.0}})
    assert high_total[0].correlation > low_total[0].correlation
