from app.projections.nfl.common import PlayerProjectionContext
from app.projections.nfl.model import project_player


def make_ctx(**overrides) -> PlayerProjectionContext:
    defaults = dict(
        player_id="p1", name="Test Player", position="QB", team="BUF", opponent="NYJ", is_home=True,
        season_usage={"pass_attempts_pg": 34, "completion_pct": 0.65, "pass_yards_per_attempt": 7.2,
                      "pass_td_rate": 0.045, "int_rate": 0.02, "rush_attempts_pg": 5, "rush_yards_per_carry": 4.5,
                      "rush_td_rate": 0.03},
        recent_usage={}, games_sampled=8,
        implied_team_total=22.0, opponent_implied_total=22.0, spread=0.0,
        matchup_zscore=0.0, wind_mph=0.0, is_dome=False, injury_status="healthy", injury_availability_prob=1.0,
    )
    defaults.update(overrides)
    return PlayerProjectionContext(**defaults)


def test_additive_breakdown_sums_to_projected_points():
    comp = project_player(make_ctx())
    total = (
        comp.base_projection + comp.vegas_adjustment + comp.usage_adjustment
        + comp.matchup_adjustment + comp.weather_adjustment + comp.injury_adjustment
    )
    assert round(total, 2) == comp.projected_points


def test_higher_implied_total_increases_qb_projection():
    low = project_player(make_ctx(implied_team_total=18.0))
    high = project_player(make_ctx(implied_team_total=28.0))
    assert high.projected_points > low.projected_points


def test_favorable_matchup_increases_projection():
    tough = project_player(make_ctx(matchup_zscore=-1.5))
    plus = project_player(make_ctx(matchup_zscore=1.5))
    assert plus.projected_points > tough.projected_points


def test_wind_hurts_qb_projection():
    calm = project_player(make_ctx(wind_mph=5.0))
    windy = project_player(make_ctx(wind_mph=25.0))
    assert windy.projected_points < calm.projected_points


def test_dome_ignores_wind():
    dome = project_player(make_ctx(wind_mph=30.0, is_dome=True))
    assert dome.weather_adjustment == 0.0


def test_injury_availability_discounts_projection():
    healthy = project_player(make_ctx(injury_availability_prob=1.0))
    questionable = project_player(make_ctx(injury_availability_prob=0.7))
    assert questionable.projected_points < healthy.projected_points
    assert questionable.injury_adjustment < 0


def test_all_positions_dispatch_without_error():
    for position in ("QB", "RB", "WR", "TE", "K", "DST"):
        comp = project_player(make_ctx(position=position))
        assert comp.position == position
        assert comp.projected_points >= 0


def test_floor_median_ceiling_ordering():
    comp = project_player(make_ctx())
    assert comp.floor <= comp.projected_points <= comp.ceiling
