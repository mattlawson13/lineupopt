"""Shared types and helper math for the per-position NFL projection
models (qb.py, rb.py, wr_te.py, kicker.py, dst.py).

Design: every position model produces an *additive, explainable* breakdown
—  base -> + vegas adjustment -> + usage adjustment -> + matchup adjustment
-> + weather adjustment -> + injury adjustment == final projection — so the
"Why?" transparency panel (spec section 34) is never a black box; it's
literally how the number was built.
"""
from __future__ import annotations

import dataclasses

from app.config.loader import get_simulation_settings

LEAGUE_AVG_IMPLIED_TOTAL = 22.0


@dataclasses.dataclass
class PlayerProjectionContext:
    player_id: str
    name: str
    position: str  # QB, RB, WR, TE, K, DST
    team: str
    opponent: str
    is_home: bool

    # Usage: dict of rate stats. Keys vary by position (see individual
    # calculators) — produced by features/usage_features.py.
    season_usage: dict = dataclasses.field(default_factory=dict)
    recent_usage: dict = dataclasses.field(default_factory=dict)  # recency-weighted, shrunk
    games_sampled: int = 0

    # Game environment (features/game_environment.py)
    implied_team_total: float = LEAGUE_AVG_IMPLIED_TOTAL
    opponent_implied_total: float = LEAGUE_AVG_IMPLIED_TOTAL
    spread: float = 0.0  # negative = favored

    # Matchup (features/matchup.py) — positive = favorable matchup for this player's position
    matchup_zscore: float = 0.0

    # Weather (0 impact if dome/unknown)
    wind_mph: float = 0.0
    temperature_f: float = 60.0
    precipitation_pct: float = 0.0
    is_dome: bool = False

    # Injury (spec section: "underestimating..."; availability_prob folds
    # QUESTIONABLE/DOUBTFUL into an expected-value discount rather than a
    # binary in/out call the model can't actually make)
    injury_status: str = "healthy"
    injury_availability_prob: float = 1.0


@dataclasses.dataclass
class ComponentProjection:
    player_id: str
    position: str
    stat_line: dict
    base_projection: float
    vegas_adjustment: float
    usage_adjustment: float
    matchup_adjustment: float
    weather_adjustment: float
    injury_adjustment: float
    snap_trend_adjustment: float = 0.0  # default 0.0 so kicker.py/dst.py (no snap-share signal) don't need updating

    @property
    def projected_points(self) -> float:
        return round(
            self.base_projection
            + self.vegas_adjustment
            + self.usage_adjustment
            + self.matchup_adjustment
            + self.weather_adjustment
            + self.injury_adjustment
            + self.snap_trend_adjustment,
            2,
        )

    @property
    def model_uncertainty(self) -> float:
        cv = get_simulation_settings()["player_variance"].get(self.position, {}).get("cv", 0.5)
        return round(max(self.projected_points, 0.0) * cv, 2)

    @property
    def floor(self) -> float:
        return round(max(self.projected_points - self.model_uncertainty, 0.0), 2)

    @property
    def ceiling(self) -> float:
        return round(self.projected_points + self.model_uncertainty * 1.4, 2)

    def why_panel(self) -> dict:
        """Matches the transparency format from spec section 34."""
        return {
            "projected_fantasy_points": self.projected_points,
            "base_projection": round(self.base_projection, 2),
            "vegas_adjustment": round(self.vegas_adjustment, 2),
            "usage_adjustment": round(self.usage_adjustment, 2),
            "matchup_adjustment": round(self.matchup_adjustment, 2),
            "weather_adjustment": round(self.weather_adjustment, 2),
            "injury_adjustment": round(self.injury_adjustment, 2),
            "snap_trend_adjustment": round(self.snap_trend_adjustment, 2),
            "model_uncertainty": self.model_uncertainty,
            "stat_line": self.stat_line,
        }


def vegas_delta(ctx: PlayerProjectionContext, points_per_implied_point: float) -> float:
    """Points added/subtracted per point the player's team's implied total
    is above/below league average, scaled per-position by
    `points_per_implied_point` (how many fantasy points this position
    typically gains for each extra point of implied team total).
    """
    return (ctx.implied_team_total - LEAGUE_AVG_IMPLIED_TOTAL) * points_per_implied_point


def usage_trend_delta(ctx: PlayerProjectionContext, base_stat_points_fn) -> float:
    """Delta between fantasy points implied by recency-weighted usage vs
    season-long usage — captures a real role change (e.g. a player who
    just took over as the lead back) without overreacting to one huge game
    (shrinkage already applied upstream in features/usage_features.py).
    """
    if not ctx.recent_usage or not ctx.season_usage or ctx.games_sampled == 0:
        return 0.0
    recent_pts = base_stat_points_fn(ctx.recent_usage)
    season_pts = base_stat_points_fn(ctx.season_usage)
    return recent_pts - season_pts


def matchup_delta(ctx: PlayerProjectionContext, sensitivity: float) -> float:
    return ctx.matchup_zscore * sensitivity


def snap_trend_delta(ctx: PlayerProjectionContext, base_points: float, damping: float = 0.5) -> float:
    """Scales the projection by how much recent offensive snap share has
    moved relative to the season rate — a genuinely separate signal from
    usage_trend_delta above (targets/touches), and typically a *leading*
    one: a back or receiver's snap count usually shifts a game or two
    before their target/touch share catches up, so this can catch a real
    role change usage_trend_delta hasn't shown yet.

    Requires nflverse's snap_counts release to have cross-referenced
    successfully for this player (see build_game_logs_by_player's
    snap_df param) — silently 0.0 when unavailable rather than guessing.
    `damping` (default 0.5, i.e. only half-credited) keeps this
    conservative since, unlike the target/touch-based signals this model
    has run with from the start, it's new and not yet validated against
    real outcomes — see the post-slate resolution work for how that
    validation is meant to happen over time.
    """
    recent_snap = ctx.recent_usage.get("snap_pct")
    season_snap = ctx.season_usage.get("snap_pct")
    if recent_snap is None or season_snap is None or season_snap <= 0.01:
        return 0.0
    ratio = max(0.5, min(1.5, recent_snap / season_snap))  # cap the swing at +/-50%
    return base_points * (ratio - 1.0) * damping


def wind_penalty(wind_mph: float, threshold: float = 12.0, per_mph: float = 0.02) -> float:
    """Wind meaningfully hurts passing/kicking above a threshold; below it,
    negligible. Returns a *multiplicative* penalty factor in (0, 1].
    """
    if wind_mph <= threshold:
        return 1.0
    excess = wind_mph - threshold
    return max(1.0 - excess * per_mph, 0.55)


def injury_delta(ctx: PlayerProjectionContext, healthy_points: float) -> float:
    if ctx.injury_availability_prob >= 0.999:
        return 0.0
    return healthy_points * (ctx.injury_availability_prob - 1.0)
