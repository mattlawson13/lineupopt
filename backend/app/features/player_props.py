"""Turns raw sportsbook player-prop lines (data_sources/player_props.py)
into a per-player market-based fantasy-points projection — a real
alternative to ensemble.py's market_projection_from_vegas(), which only
knows a player's position and team implied total, not anything about the
specific player. A prop line (e.g. "Bo Nix Over 229.5 passing yards") is a
genuine market estimate for THIS player, so where coverage exists it's a
strictly better market signal than the team-total heuristic.

Deliberately conservative in what it estimates: a book's O/U line for a
yardage/reception market is used directly as that stat's point estimate
(the line itself IS the market's expected value, by design of how books
set lines) with no devigging needed since Over/Under share one point.
player_anytime_td is a genuine yes/no market (no paired "No" price is
listed per player), so its single price is converted to an implied
probability and used AS an expected touchdown count — a standard
simplification (ignores multi-TD games), not a claim of precision.

Stats compute_dk_points doesn't have prop coverage for (interceptions,
fumbles, TD *type* for a player who could score either way) are simply
absent from the resulting partial stat line, which compute_dk_points
already treats as 0 via dict.get() — this projection is necessarily a
lower-context number, meant as one ensemble input, not a full substitute
for the component model.
"""
from __future__ import annotations

from app.data_sources.player_props import PlayerPropOutcome
from app.normalization.player_matcher import normalize_name
from app.projections.nfl.dk_points import compute_dk_points

# Which TD-type bucket a position's anytime-TD probability should be
# credited to — imprecise for a true dual-threat player, but a reasonable
# default per position group (see module docstring).
_TD_STAT_BY_POSITION = {"QB": "rush_td", "RB": "rush_td", "WR": "rec_td", "TE": "rec_td"}


def american_odds_to_prob(price: int) -> float:
    if price > 0:
        return 100.0 / (price + 100.0)
    return -price / (-price + 100.0)


def build_prop_stat_lines(outcomes_by_event: dict[str, list[PlayerPropOutcome]]) -> dict[str, dict]:
    """(normalized player name) -> partial stat line, e.g.
    {"pass_yards": 229.5, "rush_yards": 12.5, "anytime_td_prob": 0.31}.
    Multiple events are merged (a player only ever appears in one, but
    callers fetch per-event so this collects them into one lookup).
    """
    stat_lines: dict[str, dict] = {}
    for outcomes in outcomes_by_event.values():
        for o in outcomes:
            norm = normalize_name(o.player_name)
            line = stat_lines.setdefault(norm, {})
            if o.market == "player_pass_yds" and o.point is not None:
                line["pass_yards"] = o.point
            elif o.market == "player_rush_yds" and o.point is not None:
                line["rush_yards"] = o.point
            elif o.market == "player_receptions" and o.point is not None:
                line["receptions"] = o.point
            elif o.market == "player_reception_yds" and o.point is not None:
                line["rec_yards"] = o.point
            elif o.market == "player_anytime_td" and o.side == "Yes":
                line["anytime_td_prob"] = american_odds_to_prob(o.price)
    return stat_lines


def market_projection_from_props(prop_stat_line: dict, position: str) -> float | None:
    """DK fantasy points implied by this player's real prop lines, or None
    if this player has no prop coverage at all (bench/deep depth-chart
    players and DST never have props — caller should fall back to
    ensemble.market_projection_from_vegas() in that case).
    """
    if not prop_stat_line:
        return None

    stat_line = {k: v for k, v in prop_stat_line.items() if k != "anytime_td_prob"}
    td_prob = prop_stat_line.get("anytime_td_prob")
    if td_prob is not None:
        td_stat = _TD_STAT_BY_POSITION.get(position)
        if td_stat:
            stat_line[td_stat] = stat_line.get(td_stat, 0.0) + td_prob

    return compute_dk_points(stat_line)
