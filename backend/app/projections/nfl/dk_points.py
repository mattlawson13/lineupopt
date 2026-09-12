"""Converts a component stat line into DraftKings fantasy points using the
scoring rules in config/dk_scoring_nfl.yaml. Shared by the projection
engine (projected points) and the backtesting engine (actual points from
historical PlayerStat rows) — one implementation, so "how DK scores a
game" is never duplicated or allowed to drift.
"""
from __future__ import annotations

from app.config.loader import get_dk_scoring


def compute_dk_points(stat_line: dict, scoring: dict | None = None) -> float:
    s = scoring or get_dk_scoring("nfl")["scoring"]
    pts = 0.0

    # Passing
    pass_yards = stat_line.get("pass_yards", 0.0)
    pts += pass_yards * s["passing_yard"]
    pts += stat_line.get("pass_td", 0) * s["passing_td"]
    pts += stat_line.get("interceptions", 0) * s["interception_thrown"]
    if pass_yards >= s["passing_yard_bonus"]["threshold"]:
        pts += s["passing_yard_bonus"]["bonus"]

    # Rushing
    rush_yards = stat_line.get("rush_yards", 0.0)
    pts += rush_yards * s["rushing_yard"]
    pts += stat_line.get("rush_td", 0) * s["rushing_td"]
    if rush_yards >= s["rushing_yard_bonus"]["threshold"]:
        pts += s["rushing_yard_bonus"]["bonus"]

    # Receiving
    rec_yards = stat_line.get("rec_yards", 0.0)
    pts += stat_line.get("receptions", 0) * s["reception"]
    pts += rec_yards * s["receiving_yard"]
    pts += stat_line.get("rec_td", 0) * s["receiving_td"]
    if rec_yards >= s["receiving_yard_bonus"]["threshold"]:
        pts += s["receiving_yard_bonus"]["bonus"]

    # Misc offense
    pts += stat_line.get("fumbles_lost", 0) * s["fumble_lost"]
    pts += stat_line.get("two_pt", 0) * s["two_point_conversion"]
    pts += stat_line.get("off_fumble_recovery_td", 0) * s["offensive_fumble_recovery_td"]
    pts += stat_line.get("return_td", 0) * s["punt_kick_return_td"]

    # Kicker
    pts += stat_line.get("fg_0_39", 0) * s["fg_made_0_39"]
    pts += stat_line.get("fg_40_49", 0) * s["fg_made_40_49"]
    pts += stat_line.get("fg_50_plus", 0) * s["fg_made_50_plus"]
    pts += stat_line.get("fg_missed", 0) * s["fg_missed"]
    pts += stat_line.get("xp_made", 0) * s["xp_made"]

    # DST
    pts += stat_line.get("sacks", 0) * s["dst_sack"]
    pts += stat_line.get("dst_interceptions", 0) * s["dst_interception"]
    pts += stat_line.get("dst_fumble_recoveries", 0) * s["dst_fumble_recovery"]
    pts += stat_line.get("dst_td", 0) * s["dst_touchdown"]
    pts += stat_line.get("safeties", 0) * s["dst_safety"]
    pts += stat_line.get("blocked_kicks", 0) * s["dst_blocked_kick"]
    pts += stat_line.get("dst_return_td", 0) * s["dst_return_td"]
    if "points_allowed" in stat_line:
        pa = stat_line["points_allowed"]
        for tier in s["dst_points_allowed"]:
            if pa <= tier["max_points"]:
                pts += tier["bonus"]
                break

    return round(pts, 2)
