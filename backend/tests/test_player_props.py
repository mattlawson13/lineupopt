from app.data_sources.player_props import PlayerPropOutcome
from app.features.player_props import (
    american_odds_to_prob,
    build_prop_stat_lines,
    market_projection_from_props,
)


def test_american_odds_to_prob_negative():
    # -110 is the classic "vig" price, implies slightly over 50%
    assert round(american_odds_to_prob(-110), 3) == round(110 / 210, 3)


def test_american_odds_to_prob_positive():
    assert round(american_odds_to_prob(165), 3) == round(100 / 265, 3)


def test_build_prop_stat_lines_merges_markets_per_player():
    outcomes_by_event = {
        "evt1": [
            PlayerPropOutcome(player_name="Bo Nix", market="player_pass_yds", side="Over", point=229.5, price=-112),
            PlayerPropOutcome(player_name="Bo Nix", market="player_pass_yds", side="Under", point=229.5, price=-112),
            PlayerPropOutcome(player_name="Kenneth Walker III", market="player_rush_yds", side="Over", point=63.5, price=-113),
            PlayerPropOutcome(player_name="Kenneth Walker III", market="player_anytime_td", side="Yes", point=None, price=-120),
        ]
    }
    lines = build_prop_stat_lines(outcomes_by_event)
    assert lines["bo nix"]["pass_yards"] == 229.5
    assert lines["kenneth walker"]["rush_yards"] == 63.5
    assert "anytime_td_prob" in lines["kenneth walker"]


def test_market_projection_from_props_none_when_no_coverage():
    assert market_projection_from_props({}, "WR") is None


def test_market_projection_from_props_uses_partial_stat_line():
    # A WR with only receiving props (no rush props, as expected) should
    # still produce a real, positive points estimate from what IS covered.
    pts = market_projection_from_props(
        {"receptions": 5.5, "rec_yards": 58.5, "anytime_td_prob": 0.3}, "WR"
    )
    assert pts is not None and pts > 0


def test_anytime_td_credited_to_position_specific_stat():
    rb_pts = market_projection_from_props({"anytime_td_prob": 1.0}, "RB")
    wr_pts = market_projection_from_props({"anytime_td_prob": 1.0}, "WR")
    # Both rush_td and rec_td score 6 pts in DK scoring, so a full (1.0)
    # implied TD probability should award the same points regardless of
    # which bucket it's credited to.
    assert rb_pts == wr_pts
