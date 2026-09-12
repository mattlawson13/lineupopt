import numpy as np

from app.correlations.engine import CorrelationEntry
from app.simulation.distributions import gamma_ppf_from_normal
from app.simulation.game_sim import GameSimInput
from app.simulation.monte_carlo import PlayerSimInput, simulate_slate


def test_gamma_transform_preserves_mean_approximately():
    rng = np.random.default_rng(0)
    z = rng.standard_normal(200_000)
    draws = gamma_ppf_from_normal(z, mean=20.0, cv=0.5)
    assert abs(draws.mean() - 20.0) < 0.15


def test_percentiles_are_monotonic():
    rng = np.random.default_rng(1)
    z = rng.standard_normal(50_000)
    draws = gamma_ppf_from_normal(z, mean=15.0, cv=0.6)
    p10, p50, p90 = np.percentile(draws, [10, 50, 90])
    assert p10 < p50 < p90


def test_simulate_slate_respects_correlation_sign():
    games = [GameSimInput("g1", "BUF", "NYJ", home_spread=-3.0, total=46.0)]
    players = [
        PlayerSimInput("qb", "QB", "BUF", 7000, 22.0),
        PlayerSimInput("wr", "WR", "BUF", 6500, 15.0),
    ]
    corr = [CorrelationEntry("qb", "wr", "qb_own_wr", 0.6, True, 300, {})]
    result = simulate_slate(
        players, games, {"BUF": "g1", "NYJ": "g1"}, {"BUF": "home", "NYJ": "away"},
        corr, num_simulations=20000, seed=5,
    )
    realized_corr = np.corrcoef(result.player_draws["qb"], result.player_draws["wr"])[0, 1]
    assert realized_corr > 0.3  # copula correlation attenuates vs the target but should stay clearly positive


def test_simulate_slate_produces_full_summary_fields():
    games = [GameSimInput("g1", "BUF", "NYJ", home_spread=0.0, total=44.0)]
    players = [PlayerSimInput("qb", "QB", "BUF", 7000, 20.0)]
    result = simulate_slate(players, games, {"BUF": "g1", "NYJ": "g1"}, {"BUF": "home", "NYJ": "away"}, [], num_simulations=5000, seed=1)
    s = result.player_summaries["qb"]
    assert s.mean > 0
    assert s.floor <= s.median <= s.ceiling
    assert 0 <= s.prob_3x_salary <= 1
    assert set(s.percentiles.keys()) == {"10", "25", "50", "75", "90", "95", "99"}


def test_num_simulations_capped_at_config_max():
    games = [GameSimInput("g1", "BUF", "NYJ", home_spread=0.0, total=44.0)]
    players = [PlayerSimInput("qb", "QB", "BUF", 7000, 20.0)]
    result = simulate_slate(players, games, {"BUF": "g1", "NYJ": "g1"}, {"BUF": "home", "NYJ": "away"}, [], num_simulations=999_999, seed=1)
    assert result.num_simulations <= 50000
