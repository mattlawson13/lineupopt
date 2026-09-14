"""The "BUILD SLATE" pipeline — spec section 18.

Orchestrates every layer (ingestion -> normalization -> features ->
projections -> ownership -> correlations -> simulation -> optimization)
for one DraftKings slate, end to end. Implemented as a generator so the API
layer can stream real-time progress (spec: "Display progress in real
time"); every step is wrapped so a single source failing degrades with a
warning rather than aborting the whole build (spec section 29/30 — never
silently fail, but never let one flaky source block everything either).
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import datetime
import logging
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.correlations.engine import CorrelationEntry, CorrelationPlayer, build_correlation_matrix
from app.data_sources.base import SourceUnavailableError
from app.data_sources.betting import BettingSource
from app.data_sources.draftkings import DraftKingsApiSource, DraftKingsCsvSource, DraftKingsSlate
from app.data_sources.injury import InjurySource
from app.data_sources.nfl_stats import NFLStatsSource
from app.data_sources.weather import WeatherSource
from app.features import espn_adapter
from app.features.game_environment import compute_game_environment
from app.features.matchup import compute_matchup_zscores
from app.features.nflverse_adapter import build_fpts_allowed_by_team_position, build_game_logs_by_player
from app.features.usage_features import compute_usage_snapshot
from app.models.core import Game, Player, Team
from app.models.enums import InjuryStatus
from app.models.lineup import Lineup, LineupPlayer, ModelVersion, OptimizationRun
from app.models.projections import EnsembleProjection, Projection, ProjectionSource
from app.models.analytics import Correlation, OwnershipProjection
from app.models.slate import DraftKingsPlayer, DraftKingsSalary, Slate
from app.normalization.player_matcher import get_or_create_player, get_or_create_team, normalize_name, normalize_team_abbreviation
from app.optimization.diversification import DiversificationSettings, generate_portfolio
from app.optimization.dk_rules import get_contest_rules
from app.optimization.optimizer import OptimizerPlayer
from app.optimization.stacking import classify_stack
from app.ownership.model import PlayerOwnershipInput, compute_chalk_contrarian_scores, project_ownership
from app.projections.ensemble import SourceProjections, compute_ensemble, market_projection_from_vegas
from app.projections.nfl.common import PlayerProjectionContext
from app.projections.nfl.model import project_player
from app.projections.why_panel import build_why_panel
from app.simulation.game_sim import GameSimInput
from app.simulation.monte_carlo import PlayerSimInput, simulate_slate
from app.sports.nfl.stadiums import DOME_TEAMS, TEAM_STADIUM_COORDS

logger = logging.getLogger("lineupopt.ingestion")


@dataclasses.dataclass
class BuildProgressEvent:
    step: str
    status: str  # running | success | warning | error
    detail: str
    data: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class BuildOptions:
    num_simulations: int = 10000
    num_lineups: int = 20
    objective: str = "large_field_gpp"
    seed: int | None = None
    # Optional: a specific DK contest to calibrate the objective against
    # (see optimization/contest_calibration.py) — when set, the fixed
    # contest_modes weight bucket for `objective` is replaced with weights
    # scaled to this contest's real field size.
    dk_contest_id: str | None = None


def run_build_slate(
    db: Session,
    dk_draft_group_id: str | None = None,
    csv_text: str | None = None,
    options: BuildOptions | None = None,
) -> Iterator[BuildProgressEvent]:
    options = options or BuildOptions()
    run_id = f"NFL-BUILD-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}"
    logger.info("Starting slate build %s", run_id)

    # ---- 1. DK slate ---------------------------------------------------
    yield BuildProgressEvent("dk_slate", "running", "Fetching DraftKings slate...")
    try:
        dk_slate, source_used = _fetch_dk_slate(dk_draft_group_id, csv_text)
    except SourceUnavailableError as exc:
        yield BuildProgressEvent("dk_slate", "error", str(exc))
        return
    yield BuildProgressEvent(
        "dk_slate", "success", f"{len(dk_slate.players)} players found (source: {source_used})",
        {"player_count": len(dk_slate.players)},
    )

    from app.ingestion.validation import has_blocking_errors, validate_dk_slate
    validation_issues = validate_dk_slate(dk_slate)
    if validation_issues:
        errors = [i for i in validation_issues if i.severity == "error"]
        yield BuildProgressEvent(
            "data_validation",
            "error" if errors else "warning",
            f"{len(errors)} error(s), {len(validation_issues) - len(errors)} warning(s) in raw slate data"
            + (f" — e.g. {errors[0].message}" if errors else f" — e.g. {validation_issues[0].message}"),
            {"issue_count": len(validation_issues)},
        )
    if has_blocking_errors(validation_issues):
        yield BuildProgressEvent("data_validation", "error", "Blocking data-quality errors — aborting build rather than optimizing on corrupt data")
        return

    # ---- 2. Persist teams/games/players/slate --------------------------
    yield BuildProgressEvent("persist_entities", "running", "Normalizing players/teams/games...")
    slate, dk_player_rows, team_by_abbrev, game_by_teams = _persist_slate_entities(db, dk_slate, run_id)
    db.commit()
    yield BuildProgressEvent(
        "persist_entities", "success",
        f"{len(team_by_abbrev)} teams, {len(game_by_teams)} games, {len(dk_player_rows)} DK player rows",
    )

    # ---- 3. Betting lines ------------------------------------------------
    yield BuildProgressEvent("betting", "running", "Fetching Vegas lines...")
    game_env_by_game_id, betting_warning = _fetch_and_apply_betting_lines(db, game_by_teams)
    if betting_warning:
        yield BuildProgressEvent("betting", "warning", betting_warning)
    else:
        yield BuildProgressEvent("betting", "success", f"Lines applied for {len(game_env_by_game_id)} games")

    # ---- 4. Weather ------------------------------------------------------
    yield BuildProgressEvent("weather", "running", "Fetching weather forecasts...")
    weather_warnings = _fetch_and_apply_weather(db, game_by_teams)
    if weather_warnings:
        yield BuildProgressEvent("weather", "warning", "; ".join(weather_warnings[:3]))
    else:
        yield BuildProgressEvent("weather", "success", "Weather updated for all outdoor games")

    # ---- 5. Injuries -------------------------------------------------------
    yield BuildProgressEvent("injuries", "running", "Fetching injury reports...")
    injury_by_norm_name, injury_warning = _fetch_injuries(db, team_by_abbrev.keys())
    if injury_warning:
        yield BuildProgressEvent("injuries", "warning", injury_warning)
    else:
        yield BuildProgressEvent("injuries", "success", f"{len(injury_by_norm_name)} injury designations loaded")
    db.commit()  # release the write lock before the slow external nflverse fetch below

    # ---- 5b. Depth chart (current starter vs. backup) ----------------------
    yield BuildProgressEvent("depth_chart", "running", "Loading current depth charts...")
    depth_ranks, covered_team_positions, depth_warning = _load_depth_chart_ranks(slate.season)
    effective_depth_ranks = _promote_for_injuries(dk_player_rows, depth_ranks, covered_team_positions, injury_by_norm_name)
    if depth_warning:
        yield BuildProgressEvent("depth_chart", "warning", depth_warning)
    else:
        yield BuildProgressEvent("depth_chart", "success", f"Depth-chart rank resolved for {len(effective_depth_ranks)} DK players")

    # ---- 6. Historical usage / matchup data --------------------------------
    yield BuildProgressEvent("historical_stats", "running", "Loading historical usage data (nflverse)...")
    game_logs, fpts_allowed, stats_warning = _load_historical_stats(slate.season, slate.week)
    if stats_warning:
        yield BuildProgressEvent("historical_stats", "warning", stats_warning)
    else:
        yield BuildProgressEvent("historical_stats", "success", f"{len(game_logs)} player game-log histories loaded")
    matchup_zscores = compute_matchup_zscores(fpts_allowed) if fpts_allowed else {}

    # ---- 7. Projection ensemble --------------------------------------------
    yield BuildProgressEvent("projections", "running", "Building projection ensemble...")
    model_version = ModelVersion(
        sport="nfl", version_tag=run_id, component="projection",
        description="Component-based NFL model + market + manual imports",
        config_snapshot={}, is_active=True,
    )
    db.add(model_version)
    db.flush()

    ensemble_by_player_id, why_panels = _build_projections(
        db, slate, dk_player_rows, team_by_abbrev, game_by_teams, game_env_by_game_id,
        injury_by_norm_name, game_logs, matchup_zscores, model_version, effective_depth_ranks,
    )
    yield BuildProgressEvent("projections", "success", f"Ensemble projections built for {len(ensemble_by_player_id)} players")
    db.commit()

    # ---- 8. Ownership --------------------------------------------------------
    yield BuildProgressEvent("ownership", "running", "Projecting ownership...")
    ownership_by_player_id = _build_ownership(db, slate, dk_player_rows, ensemble_by_player_id, game_env_by_game_id, injury_by_norm_name)
    yield BuildProgressEvent("ownership", "success", f"Ownership projected for {len(ownership_by_player_id)} players")
    db.commit()

    # ---- 9. Correlations -------------------------------------------------
    yield BuildProgressEvent("correlations", "running", "Building correlation matrix...")
    correlation_entries = _build_correlations(db, slate, dk_player_rows, game_env_by_game_id)
    yield BuildProgressEvent("correlations", "success", f"{len(correlation_entries)} pairwise correlations computed")
    db.commit()

    # ---- 10. Monte Carlo simulation ---------------------------------------
    yield BuildProgressEvent("simulation", "running", f"Running {options.num_simulations:,} simulations...")
    sim_result, sim_run = _run_simulation(
        db, slate, dk_player_rows, ensemble_by_player_id, game_by_teams, game_env_by_game_id, correlation_entries, options
    )
    yield BuildProgressEvent(
        "simulation", "success",
        f"{sim_result.num_simulations:,} simulations completed in {sim_result.duration_seconds}s",
    )
    db.commit()

    # ---- 11. Optimize lineups ------------------------------------------------
    yield BuildProgressEvent("optimize", "running", f"Generating {options.num_lineups} lineups...")
    opt_run, lineups, calibration_note = _optimize_lineups(
        db, slate, dk_player_rows, ensemble_by_player_id, ownership_by_player_id, sim_result, sim_run, options,
        game_env_by_game_id, correlation_entries,
    )
    optimize_detail = f"{len(lineups)} lineups generated"
    if calibration_note:
        optimize_detail += f" — {calibration_note}"
    yield BuildProgressEvent("optimize", "success", optimize_detail)
    db.commit()

    # ---- 12. AI-rank / explain --------------------------------------------
    yield BuildProgressEvent("ai_rank", "running", "Ranking and explaining lineups...")
    from app.ai.lineup_evaluator import evaluate_and_rank_lineups
    evaluate_and_rank_lineups(db, lineups, ownership_by_player_id)
    yield BuildProgressEvent("ai_rank", "success", f"{len(lineups)} lineups ranked and explained")

    db.commit()
    yield BuildProgressEvent(
        "complete", "success", f"Slate build {run_id} complete",
        {"slate_id": slate.id, "optimization_run_id": opt_run.id, "num_lineups": len(lineups)},
    )


# ------------------------------------------------------------------------
# Step implementations
# ------------------------------------------------------------------------

def _fetch_dk_slate(dk_draft_group_id: str | None, csv_text: str | None) -> tuple[DraftKingsSlate, str]:
    if csv_text:
        source = DraftKingsCsvSource()
        result = source.parse(csv_text, dk_draft_group_id or "csv_import")
        return result.data, "csv_import"
    if not dk_draft_group_id:
        raise SourceUnavailableError("No DK draft group id or CSV provided")
    api = DraftKingsApiSource()
    result = api.get_draftables(dk_draft_group_id)
    return result.data, "dk_api"


def _persist_slate_entities(db: Session, dk_slate: DraftKingsSlate, run_id: str):
    existing = db.execute(select(Slate).where(Slate.dk_draft_group_id == dk_slate.dk_draft_group_id)).scalar_one_or_none()
    now = datetime.datetime.now(datetime.timezone.utc)
    start_times = [p.game_start_time_utc for p in dk_slate.players if p.game_start_time_utc]
    earliest = min(start_times) if start_times else now

    if existing:
        slate = existing
    else:
        slate = Slate(
            sport="nfl", contest_type=dk_slate.contest_type, dk_draft_group_id=dk_slate.dk_draft_group_id,
            name=f"NFL Slate {dk_slate.dk_draft_group_id}", season=earliest.year,
            week=_resolve_nfl_week(earliest.year, earliest), start_time_utc=earliest, game_ids=[],
            source="csv_import" if not dk_slate.players[0].dk_draftable_id else "dk_api",
            imported_at=now,
        )
        db.add(slate)
        try:
            db.flush()
        except IntegrityError:
            # Two builds for the same draft group started at almost the
            # same instant (e.g. a double-tap) — the other one won the
            # race on the dk_draft_group_id unique constraint. Fall back
            # to using its row instead of erroring out.
            db.rollback()
            slate = db.execute(
                select(Slate).where(Slate.dk_draft_group_id == dk_slate.dk_draft_group_id)
            ).scalar_one()

    team_by_abbrev: dict[str, Team] = {}
    game_by_teams: dict[tuple[str, str], Game] = {}
    dk_player_rows: list[DraftKingsPlayer] = []

    for row in dk_slate.players:
        team_abbrev = normalize_team_abbreviation(row.team_abbreviation)
        opp_abbrev = normalize_team_abbreviation(row.opponent_abbreviation) if row.opponent_abbreviation else ""
        if team_abbrev not in team_by_abbrev:
            team_by_abbrev[team_abbrev] = get_or_create_team(db, "nfl", team_abbrev)
        if opp_abbrev and opp_abbrev not in team_by_abbrev:
            team_by_abbrev[opp_abbrev] = get_or_create_team(db, "nfl", opp_abbrev)

        game_key = tuple(sorted([team_abbrev, opp_abbrev])) if opp_abbrev else (team_abbrev,)
        if opp_abbrev and game_key not in game_by_teams:
            game = db.execute(
                select(Game).where(
                    Game.sport == "nfl", Game.season == slate.season, Game.week == slate.week,
                    Game.home_team_id.in_([team_by_abbrev[team_abbrev].id, team_by_abbrev[opp_abbrev].id]),
                    Game.away_team_id.in_([team_by_abbrev[team_abbrev].id, team_by_abbrev[opp_abbrev].id]),
                )
            ).scalar_one_or_none()
            if not game:
                game = Game(
                    sport="nfl", season=slate.season, week=slate.week,
                    home_team_id=team_by_abbrev[team_abbrev].id, away_team_id=team_by_abbrev[opp_abbrev].id,
                    kickoff_utc=row.game_start_time_utc or slate.start_time_utc,
                    is_dome=team_abbrev in DOME_TEAMS,
                )
                db.add(game)
                db.flush()
            game_by_teams[game_key] = game

        player = get_or_create_player(db, "nfl", row.display_name, row.position, team_by_abbrev.get(team_abbrev), row.dk_player_id)

        dk_player = db.execute(
            select(DraftKingsPlayer).where(DraftKingsPlayer.slate_id == slate.id, DraftKingsPlayer.dk_player_id == row.dk_player_id)
        ).scalar_one_or_none()
        game = game_by_teams.get(game_key)
        if not dk_player:
            dk_player = DraftKingsPlayer(
                slate_id=slate.id, player_id=player.id, dk_player_id=row.dk_player_id,
                display_name=row.display_name, dk_position=row.position, team_abbreviation=team_abbrev,
                opponent_abbreviation=opp_abbrev, game_id=game.id if game else None,
                roster_status=row.roster_status,
            )
            db.add(dk_player)
            db.flush()
        else:
            dk_player.roster_status = row.roster_status

        db.add(DraftKingsSalary(dk_player_id_fk=dk_player.id, salary=row.salary))
        dk_player_rows.append(dk_player)

    slate.game_ids = [g.id for g in game_by_teams.values()]
    return slate, dk_player_rows, team_by_abbrev, game_by_teams


def _estimate_nfl_week(dt: datetime.datetime) -> int:
    season_start = datetime.datetime(dt.year if dt.month >= 8 else dt.year - 1, 9, 4, tzinfo=datetime.timezone.utc)
    delta_days = (dt - season_start).days
    return max(1, min(18, delta_days // 7 + 1))


def _resolve_nfl_week(season: int, dt: datetime.datetime) -> int:
    """Real week number for `dt`, from ESPN's own week date ranges
    (espn_adapter.resolve_week) — falls back to the September-4th
    calendar estimate only if ESPN can't be reached. The estimate is
    wrong every year the real season opener lands on a different date
    (confirmed live for 2026: it put a real week-1 Monday game one week
    into "week 2", and the next week's Sunday slate into "week 3").
    """
    resolved = espn_adapter.resolve_week(season, dt)
    return resolved if resolved is not None else _estimate_nfl_week(dt)


def _fetch_and_apply_betting_lines(db: Session, game_by_teams: dict) -> tuple[dict, str | None]:
    from app.models.context_data import BettingLine
    from app.sports.nfl.team_names import FULL_NAME_TO_ABBREV

    game_env: dict[str, dict] = {}
    matched_count = 0
    try:
        source = BettingSource()
        result = source.get_nfl_lines()
        # Key odds by (home_abbrev, away_abbrev) rather than raw sportsbook
        # full names — matching those against DK's abbreviations via a
        # substring check (the previous approach) never actually matched
        # anything (e.g. "CIN" is not a substring of "Cincinnati Bengals"),
        # so every game silently fell back to neutral defaults despite
        # reporting "success".
        by_abbrev = {}
        for line in result.data:
            home_abbrev = FULL_NAME_TO_ABBREV.get(line.home_team)
            away_abbrev = FULL_NAME_TO_ABBREV.get(line.away_team)
            if home_abbrev and away_abbrev:
                by_abbrev[(home_abbrev, away_abbrev)] = line

        for key, game in game_by_teams.items():
            match = by_abbrev.get((game.home_team.abbreviation, game.away_team.abbreviation))
            if match and match.spread_home is not None and match.total is not None:
                env = compute_game_environment(match.spread_home, match.total)
                game_env[game.id] = dataclasses.asdict(env)
                db.add(BettingLine(
                    game_id=game.id, book=match.book, spread_home=match.spread_home, total=match.total,
                    implied_total_home=env.home_implied_total, implied_total_away=env.away_implied_total,
                ))
                matched_count += 1
            else:
                game_env[game.id] = dataclasses.asdict(compute_game_environment(0.0, 44.0))

        if matched_count < len(game_by_teams):
            return game_env, f"Matched Vegas lines for {matched_count}/{len(game_by_teams)} games — rest using neutral defaults"
        return game_env, None
    except SourceUnavailableError as exc:
        for game in game_by_teams.values():
            game_env[game.id] = dataclasses.asdict(compute_game_environment(0.0, 44.0))
        return game_env, f"{exc} — using neutral (0 spread / 44 total) defaults"


def _fetch_and_apply_weather(db: Session, game_by_teams: dict) -> list[str]:
    warnings: list[str] = []
    source = WeatherSource()
    from app.models.context_data import Weather

    def _fetch_one(game):
        home_abbrev = game.home_team.abbreviation
        is_dome = home_abbrev in DOME_TEAMS
        coords = TEAM_STADIUM_COORDS.get(home_abbrev)
        if not coords:
            return game, None, f"No stadium coordinates for {home_abbrev}"
        try:
            result = source.get_forecast_for_kickoff(coords[0], coords[1], game.kickoff_utc, is_dome=is_dome)
            return game, result.data, None
        except SourceUnavailableError as exc:
            return game, None, str(exc)

    games = list(game_by_teams.values())
    # Each game's forecast is an independent HTTP call (different stadium
    # coordinates) with no dependency on any other game, but fetching them
    # one at a time was the single largest chunk of a build's wall-clock
    # time — ~20s of a ~44s build on a full 13-game slate — despite every
    # call succeeding. Unlike the ESPN injury case (dead time from calls
    # guaranteed to fail), this is real, successful work; the fix is
    # concurrency, not a circuit breaker. DB writes stay on the main
    # thread/session afterward — SQLAlchemy sessions aren't thread-safe.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(games)) or 1) as pool:
        results = list(pool.map(_fetch_one, games))

    for game, forecast, warning in results:
        if warning:
            warnings.append(warning)
            continue
        db.add(Weather(
            game_id=game.id, temperature_f=forecast.temperature_f, wind_mph=forecast.wind_mph,
            wind_direction_deg=forecast.wind_direction_deg, precipitation_pct=forecast.precipitation_pct,
            precipitation_type=forecast.precipitation_type, humidity_pct=forecast.humidity_pct, is_forecast=True,
        ))
    return warnings


_INJURY_CIRCUIT_BREAKER_THRESHOLD = 2  # see comment below


def _fetch_injuries(db: Session, team_abbrevs) -> tuple[dict, str | None]:
    source = InjurySource()
    out: dict[str, str] = {}
    failures = 0
    attempted = 0
    team_list = list(team_abbrevs)
    for abbrev in team_list:
        attempted += 1
        try:
            result = source.get_team_injuries(abbrev)
            for row in result.data:
                out[normalize_name(row.player_name)] = row.status
        except SourceUnavailableError:
            failures += 1
            # ESPN's block (Akamai bot protection on some hosting IP
            # ranges) is host-wide, not per-team, so once a couple of
            # teams fail identically the rest are guaranteed to fail the
            # same way. Without this, a 24-32 team Classic slate burned
            # 20-30+ seconds of enforced per-request spacing (1s/team)
            # on calls that could never succeed — pure dead time on every
            # single build.
            if failures >= _INJURY_CIRCUIT_BREAKER_THRESHOLD and failures == attempted:
                break

    warning = None
    if failures > 0 and failures == attempted:
        # site.api.espn.com (data_sources/injury.py) is blocked in this
        # environment, but sports.core.api.espn.com — the same underlying
        # ESPN injury data, a different host — is not (see
        # features/espn_adapter.py's module docstring for why two ESPN
        # subdomains behave differently here). Real fallback, not a second
        # guess: only used because the primary source just failed outright.
        try:
            espn_out = espn_adapter.fetch_all_injuries(team_list)
        except Exception as exc:  # noqa: BLE001 — best-effort fallback, never break the build
            espn_out = {}
            logging.getLogger(__name__).warning("espn_adapter.fetch_all_injuries failed: %s", exc)
        if espn_out:
            out.update(espn_out)
            warning = f"ESPN's usual injury endpoint is blocked here — used {len(espn_out)} designation(s) from ESPN's core API instead"
        else:
            warning = "ESPN injury endpoint unreachable from this environment — assuming all players healthy unless manually reported"

    # Manual reports (POST /api/injuries/import) always win over either
    # live feed for a given player. Ordered oldest-first so the latest
    # report for a given player is what ends up in the dict.
    from app.models.context_data import PlayerInjury

    manual_rows = db.execute(
        select(PlayerInjury, Player).join(Player, PlayerInjury.player_id == Player.id).order_by(PlayerInjury.reported_at)
    ).all()
    manual_count = 0
    for injury, player in manual_rows:
        out[normalize_name(player.full_name)] = injury.status
        manual_count += 1
    if warning and manual_count and not out:
        warning = f"ESPN injury endpoints unreachable — using {manual_count} manually-reported designation(s); everyone else assumed healthy"
    return out, warning


def _load_historical_stats(season: int, week: int) -> tuple[dict, dict, str | None]:
    source = NFLStatsSource()
    df = None
    lookup_season = None
    last_exc: SourceUnavailableError | None = None
    # Try the current season first, falling back a few years if nflverse
    # hasn't published it yet (e.g. before week 1) — one small per-season
    # fetch (~5.5MB) at a time rather than one huge combined-history fetch.
    for candidate in range(season, season - 4, -1):
        try:
            df = source.get_player_stats(candidate).data
            lookup_season = candidate
            break
        except SourceUnavailableError as exc:
            last_exc = exc

    if df is None:
        return {}, {}, f"{last_exc} — projections will fall back to positional priors"

    # Snap share is a genuinely separate, additive signal from targets/
    # touches (see usage_features.py and the position models' snap-trend
    # adjustment) — it's optional here (None on failure) rather than
    # blocking the build, since the whole projection pipeline already
    # works without it and this is a newer, less battle-tested input.
    snap_df = None
    try:
        snap_df = source.get_snap_counts(lookup_season).data
    except SourceUnavailableError:
        pass

    through = week if lookup_season == season else None
    game_logs = build_game_logs_by_player(df, lookup_season, through_week=through, snap_df=snap_df)
    fpts_allowed = build_fpts_allowed_by_team_position(df, lookup_season, through_week=through)
    warning = None if lookup_season == season else f"No {season} data yet — using {lookup_season} historical rates"

    # nflverse hasn't caught up to `season` yet, but real games may already
    # have been played this season — ESPN's core API (features/espn_adapter.py)
    # is reachable from this environment and NOT similarly stale, so use it
    # to fill in the actual completed weeks rather than relying entirely on
    # last season's rates. Best-effort: any failure here just leaves the
    # nflverse-only fallback above in place.
    if lookup_season != season and week > 1:
        try:
            espn_rows = espn_adapter.fetch_season_rows(season, week)
        except espn_adapter.ESPNUnavailableError:
            espn_rows = []
        if espn_rows:
            espn_logs = espn_adapter.build_game_logs_by_player(espn_rows)
            for key, games in espn_logs.items():
                game_logs.setdefault(key, []).extend(games)  # appended = most recent, for recency weighting
            fpts_allowed.update(espn_adapter.build_fpts_allowed_by_team_position(espn_rows))
            warning = (
                f"Using {len(espn_rows)} real {season} game logs (ESPN) through week {week - 1}; "
                f"earlier history from {lookup_season}"
            )

    # nflverse's player_stats has never covered team defense at all (not a
    # staleness gap like the block above — offense-only by design), so DST
    # game logs have always been empty and projections/nfl/dst.py has
    # always run on hardcoded league-average constants. ESPN's core API
    # has real per-game team-defense stats regardless of whether the
    # nflverse fallback above matched this season or not.
    if week > 1:
        try:
            dst_rows = espn_adapter.fetch_season_defense_rows(season, week)
        except espn_adapter.ESPNUnavailableError:
            dst_rows = []
        if dst_rows:
            game_logs.update(espn_adapter.build_dst_game_logs_by_team(dst_rows))

    return game_logs, fpts_allowed, warning


_UNLISTED_DEPTH_RANK = 99  # sentinel: on the roster, but not on the tracked depth chart at all


_DEPTH_CHART_POSITION_MAP = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE"}
# Deliberately NOT the same as NFL_POSITION_MAP (features/nflverse_adapter.py),
# which folds FB into RB — correct for aggregating a fullback's rushing/
# receiving stats into the RB *usage* model, but wrong here: depth charts
# track FB as its own position group with its own independent rank
# ordering, so a fullback who happens to be "FB rank 1" (often literally
# the only fullback on the roster) is not the same thing as "RB rank 1" —
# treating it as such gave a real fullback (0-2 touches/game) the same
# undiscounted "starter" multiplier as the actual featured back. Excluding
# FB here means a fullback finds no RB-group rank and correctly falls into
# the "not ranked, but the position IS covered" branch in
# _promote_for_injuries, landing on the RB floor discount instead.


def _load_depth_chart_ranks(season: int) -> tuple[dict[tuple[str, str], int], set[tuple[str, str]], str | None]:
    """(normalized_name, position) -> current depth-chart rank (1 = starter),
    from nflverse's most recent depth-chart snapshot, plus the set of
    (team, position) groups the chart actually covers.

    Without this, a backup QB/RB/WR who has real historical game logs from
    the last time they *were* a starter (e.g. an injury-forced start two
    seasons ago) gets projected full starter volume off that stale usage
    data — nothing else in the pipeline knows their current role changed.
    The coverage set matters separately: a DK-listed player who is on the
    roster but doesn't appear anywhere in a *covered* team+position group
    (e.g. an emergency 3rd/4th-string arm never included on the tracked
    depth chart) is not "unknown data" — DK pricing them at the salary
    floor already tells us they're a scrub — so they get treated as
    ranked below the tracked backups, not left undiscounted. See
    `_depth_chart_multiplier`.
    """
    try:
        # get_depth_chart() already stream-parses down to just the latest
        # snapshot (see its docstring) — no further date filtering needed.
        df = NFLStatsSource().get_depth_chart(season).data
    except SourceUnavailableError as exc:
        return {}, set(), f"Depth chart unavailable — cannot distinguish current starters from backups: {exc}"
    if df.empty:
        return {}, set(), "Depth chart empty"

    ranks: dict[tuple[str, str], int] = {}
    covered: set[tuple[str, str]] = set()
    for _, row in df.iterrows():
        position = _DEPTH_CHART_POSITION_MAP.get(row["pos_abb"])
        if not position or not isinstance(row["team"], str):
            continue
        team_abbrev = normalize_team_abbreviation(row["team"])
        covered.add((team_abbrev, position))
        if not isinstance(row["player_name"], str):
            continue
        key = (normalize_name(row["player_name"]), position)
        rank = int(row["pos_rank"])
        if key not in ranks or rank < ranks[key]:
            ranks[key] = rank
    return ranks, covered, None


_UNAVAILABLE_INJURY_STATUSES = {"out", "ir", "pup", "suspended"}


def _promote_for_injuries(
    dk_player_rows, depth_ranks: dict, covered_team_positions: set, injury_by_norm_name: dict,
) -> dict[str, int]:
    """Effective depth-chart rank per DK player row id: a backup ranked
    behind a player who is OUT/IR/etc. gets promoted (rank - 1 per
    unavailable player above them) so they're valued like the starter
    they're about to be, not discounted like a career backup. A player
    whose team+position group is tracked but who isn't listed in it gets
    the sentinel worst rank instead of being skipped — see
    `_load_depth_chart_ranks`.
    """
    groups: dict[tuple[str, str], list[tuple[int, str, object]]] = {}
    for row in dk_player_rows:
        if row.dk_position not in ("QB", "RB", "WR", "TE"):
            continue
        norm = normalize_name(row.display_name)
        rank = depth_ranks.get((norm, row.dk_position))
        if rank is None:
            if (row.team_abbreviation, row.dk_position) not in covered_team_positions:
                continue
            rank = _UNLISTED_DEPTH_RANK
        groups.setdefault((row.team_abbreviation, row.dk_position), []).append((rank, norm, row))

    effective: dict[str, int] = {}
    for members in groups.values():
        members.sort(key=lambda m: m[0])
        promote_by = 0
        for rank, norm, row in members:
            effective[row.id] = max(rank - promote_by, 1)
            if injury_by_norm_name.get(norm) in _UNAVAILABLE_INJURY_STATUSES:
                promote_by += 1
    return effective


_DEPTH_CHART_AVAILABILITY = {
    "QB": {1: 1.0, 2: 0.05, 3: 0.02},
    "RB": {1: 1.0, 2: 0.5, 3: 0.15, 4: 0.05},
    "WR": {1: 1.0, 2: 0.85, 3: 0.65, 4: 0.35, 5: 0.15},
    "TE": {1: 1.0, 2: 0.3, 3: 0.1},
}
_DEPTH_CHART_FLOOR = {"QB": 0.02, "RB": 0.05, "WR": 0.1, "TE": 0.1}


def _depth_chart_multiplier(position: str, effective_rank: int | None) -> float:
    """Fraction of a "full starter" projection this depth-chart slot should
    actually get. An unknown rank (name-matching miss, practice-squad
    elevation not yet reflected, etc.) is deliberately NOT discounted — a
    false "no data" should never zero out what might be a real starter.
    """
    if effective_rank is None:
        return 1.0
    table = _DEPTH_CHART_AVAILABILITY.get(position)
    if not table:
        return 1.0
    return table.get(effective_rank, _DEPTH_CHART_FLOOR.get(position, 0.1))


def _build_projections(
    db, slate, dk_player_rows, team_by_abbrev, game_by_teams, game_env_by_game_id,
    injury_by_norm_name, game_logs, matchup_zscores, model_version, effective_depth_ranks,
):
    proj_source_model = _get_or_create_projection_source(db, "proprietary_model", "model")
    ensemble_by_player_id: dict[str, dict] = {}
    why_panels: dict[str, dict] = {}

    for dk_row in dk_player_rows:
        player: Player = db.get(Player, dk_row.player_id)
        position = dk_row.dk_position
        if position not in ("QB", "RB", "WR", "TE", "K", "DST"):
            continue

        team = team_by_abbrev.get(dk_row.team_abbreviation)
        game = db.get(Game, dk_row.game_id) if dk_row.game_id else None
        env = game_env_by_game_id.get(dk_row.game_id, {})
        is_home = game is not None and team is not None and game.home_team_id == team.id

        # DST rows are keyed by team abbreviation (features/espn_adapter.py's
        # build_dst_game_logs_by_team), not a normalized display name — DK's
        # own DST naming ("Eagles" vs "Philadelphia Eagles" vs "PHI") isn't
        # worth guessing at when dk_row already carries the team abbreviation
        # directly, already normalized the same way (see line ~276 above).
        key = (dk_row.team_abbreviation, position) if position == "DST" else (normalize_name(dk_row.display_name), position)
        logs = game_logs.get(key, [])
        season_usage = compute_usage_snapshot(logs)
        recent_usage = compute_usage_snapshot(logs[-3:]) if len(logs) >= 1 else {}

        norm_name = normalize_name(dk_row.display_name)
        injury_status = injury_by_norm_name.get(norm_name, "healthy")
        injury_availability = {"healthy": 1.0, "questionable": 0.9, "doubtful": 0.4, "out": 0.0, "ir": 0.0}.get(injury_status, 1.0)

        depth_rank = effective_depth_ranks.get(dk_row.id)
        depth_multiplier = _depth_chart_multiplier(position, depth_rank)
        availability = injury_availability * depth_multiplier

        mz = matchup_zscores.get((dk_row.opponent_abbreviation, position), 0.0)

        ctx = PlayerProjectionContext(
            player_id=player.id, name=dk_row.display_name, position=position,
            team=dk_row.team_abbreviation, opponent=dk_row.opponent_abbreviation, is_home=is_home,
            season_usage=season_usage, recent_usage=recent_usage, games_sampled=len(logs),
            implied_team_total=env.get("home_implied_total" if is_home else "away_implied_total", 22.0),
            opponent_implied_total=env.get("away_implied_total" if is_home else "home_implied_total", 22.0),
            spread=env.get("home_spread", 0.0) if is_home else -env.get("home_spread", 0.0),
            matchup_zscore=mz,
            wind_mph=0.0, is_dome=team_by_abbrev.get(dk_row.team_abbreviation) is not None and dk_row.team_abbreviation in DOME_TEAMS,
            injury_status=injury_status, injury_availability_prob=availability,
        )
        component = project_player(ctx)

        # market_projection_from_vegas() only knows the position and the
        # team's implied total — it has no idea WHICH player is nominally
        # at that position, so on its own it silently assumes whoever it
        # is will see starter-level, fully-healthy usage. That's wrong for
        # a backup: e.g. a backup QB behind an expensive starter got the
        # same ~95%-of-team-implied-total market number as the actual
        # starter, which (even at the ensemble's modest market weight) was
        # enough to pull a correctly near-zero model projection up to a
        # real, biddable number — reported live: a backup QB projected at
        # 5.5 pts on a $8,600 salary and showing up in generated lineups.
        # Scaling by the same availability (injury x depth-chart) multiplier
        # already applied to the model component fixes it at the source
        # rather than requiring the ensemble weight to be re-tuned.
        market = market_projection_from_vegas(position, ctx.implied_team_total, ctx.opponent_implied_total) * availability
        sources = SourceProjections(model_projection=component.projected_points, model_std_dev=component.model_uncertainty, market_projection=market)
        ensemble = compute_ensemble(sources, position)

        db.add(Projection(
            slate_id=slate.id, player_id=player.id, dk_player_id_fk=dk_row.id, source_id=proj_source_model.id,
            model_version_id=model_version.id, projected_points=component.projected_points,
            floor=component.floor, median=component.projected_points, ceiling=component.ceiling,
            std_dev=component.model_uncertainty, confidence=0.85,
            inputs={
                "season_usage": season_usage, "recent_usage": recent_usage,
                "depth_chart_rank": depth_rank, "depth_chart_multiplier": depth_multiplier,
            },
            component_breakdown=component.why_panel(),
        ))
        db.add(EnsembleProjection(
            slate_id=slate.id, player_id=player.id, model_version_id=model_version.id,
            market_projection=market, model_projection=component.projected_points,
            ensemble_projection=ensemble.ensemble_projection, floor=ensemble.floor, median=ensemble.median,
            ceiling=ensemble.ceiling, std_dev=ensemble.std_dev, weights_used=ensemble.weights_used,
            component_breakdown=component.why_panel(),
        ))

        ensemble_by_player_id[player.id] = {
            "ensemble": ensemble, "component": component, "dk_row": dk_row, "is_home": is_home,
            "env": env, "injury_status": injury_status, "depth_chart_multiplier": depth_multiplier,
        }
        why_panels[player.id] = build_why_panel(component, ensemble)

    return ensemble_by_player_id, why_panels


def _get_or_create_projection_source(db: Session, name: str, kind: str) -> ProjectionSource:
    existing = db.execute(select(ProjectionSource).where(ProjectionSource.name == name)).scalar_one_or_none()
    if existing:
        return existing
    source = ProjectionSource(name=name, kind=kind, default_confidence=0.85)
    db.add(source)
    db.flush()
    return source


def _build_ownership(db, slate, dk_player_rows, ensemble_by_player_id, game_env_by_game_id, injury_by_norm_name):
    inputs = []
    for dk_row in dk_player_rows:
        info = ensemble_by_player_id.get(dk_row.player_id)
        if not info:
            continue
        env = info["env"]
        is_home = info["is_home"]
        inputs.append(PlayerOwnershipInput(
            player_id=dk_row.player_id, position=dk_row.dk_position,
            salary=dk_row.salaries[-1].salary if dk_row.salaries else 0,
            ensemble_projection=info["ensemble"].ensemble_projection,
            implied_team_total=env.get("home_implied_total" if is_home else "away_implied_total", 22.0),
            spread=env.get("home_spread", 0.0) if is_home else -env.get("home_spread", 0.0),
            recent_avg_points=None, injury_status=info["injury_status"],
        ))

    results = project_ownership(inputs, slate.contest_type)
    chalk = compute_chalk_contrarian_scores(results)
    out = {}
    for r in results:
        leverage = None  # optimal% comes from simulation/portfolio pass; left null until that runs
        row = OwnershipProjection(
            slate_id=slate.id, player_id=r.player_id, projected_ownership_pct=r.projected_ownership_pct,
            chalk_score=chalk[r.player_id]["chalk_score"], contrarian_score=chalk[r.player_id]["contrarian_score"],
            source="proprietary_model", feature_breakdown=r.feature_breakdown,
        )
        db.add(row)
        out[r.player_id] = row
    return out


def _build_correlations(db, slate, dk_player_rows, game_env_by_game_id):
    corr_players = [
        CorrelationPlayer(player_id=r.player_id, position=r.dk_position, team=r.team_abbreviation, opponent=r.opponent_abbreviation)
        for r in dk_player_rows if r.player_id
    ]
    game_context = {}
    for r in dk_player_rows:
        env = game_env_by_game_id.get(r.game_id, {})
        if env:
            game_context[r.team_abbreviation] = {"total": env.get("total"), "spread": env.get("home_spread")}

    entries = build_correlation_matrix(corr_players, historical_series=None, game_context=game_context)
    for e in entries:
        db.add(Correlation(
            slate_id=slate.id, player_a_id=e.player_a_id, player_b_id=e.player_b_id,
            relationship_type=e.relationship_type, correlation=e.correlation,
            is_empirical=e.is_empirical, sample_games=e.sample_games, condition=e.condition,
        ))
    return entries


def _run_simulation(db, slate, dk_player_rows, ensemble_by_player_id, game_by_teams, game_env_by_game_id, correlation_entries, options: BuildOptions):
    from app.models.analytics import PlayerSimulationResult, SimulationRun

    players = []
    team_to_game = {}
    team_to_role = {}
    for game in game_by_teams.values():
        team_to_game[game.home_team.abbreviation] = game.id
        team_to_game[game.away_team.abbreviation] = game.id
        team_to_role[game.home_team.abbreviation] = "home"
        team_to_role[game.away_team.abbreviation] = "away"

    for r in dk_player_rows:
        info = ensemble_by_player_id.get(r.player_id)
        if not info:
            continue
        players.append(PlayerSimInput(
            player_id=r.player_id, position=r.dk_position, team=r.team_abbreviation,
            salary=r.salaries[-1].salary if r.salaries else 0, mean_projection=info["ensemble"].ensemble_projection,
        ))

    games = []
    for g in game_by_teams.values():
        g_env = game_env_by_game_id.get(g.id, {})
        games.append(GameSimInput(
            game_id=g.id, home_team=g.home_team.abbreviation, away_team=g.away_team.abbreviation,
            home_spread=g_env.get("home_spread", 0.0), total=g_env.get("total", 44.0),
        ))

    sim_run = SimulationRun(slate_id=slate.id, num_simulations=options.num_simulations, random_seed=options.seed, status="running")
    db.add(sim_run)
    db.flush()

    result = simulate_slate(players, games, team_to_game, team_to_role, correlation_entries, options.num_simulations, options.seed)

    for pid, summary in result.player_summaries.items():
        db.add(PlayerSimulationResult(
            simulation_run_id=sim_run.id, player_id=pid, mean=summary.mean, median=summary.median,
            std_dev=summary.std_dev, floor=summary.floor, ceiling=summary.ceiling, percentiles=summary.percentiles,
            prob_3x_salary=summary.prob_3x_salary, prob_4x_salary=summary.prob_4x_salary,
            prob_5x_salary=summary.prob_5x_salary, prob_6x_salary=summary.prob_6x_salary,
            prob_top1pct=summary.prob_top1pct, prob_top5pct=summary.prob_top5pct,
        ))
    sim_run.status = "completed"
    sim_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
    return result, sim_run


def _compute_correlation_scores(correlation_entries, player_ids: set[str]) -> dict[str, float]:
    """Per-player proxy for "how much GPP correlation value does this
    player carry" — sums their correlation against every QB relationship
    they're part of (their own team's QB, or as a bring-back piece against
    an opposing QB). This is what actually plugs the `correlation` weight
    into the optimizer's objective; previously it was multiplied by a
    literal 0 (a stubbed-out placeholder that never got finished), so
    large-field GPP lineups weren't rewarded for real correlation beyond
    whatever the forced QB-stack constraint alone produced.
    """
    scores: dict[str, float] = {pid: 0.0 for pid in player_ids}
    for e in correlation_entries:
        if not e.relationship_type.startswith("qb_"):
            continue
        if e.player_a_id in scores:
            scores[e.player_a_id] += e.correlation
        if e.player_b_id in scores:
            scores[e.player_b_id] += e.correlation
    return scores


def _resolve_contest_calibration(objective: str, dk_contest_id: str | None) -> tuple[dict | None, str | None]:
    """(weights_override, note) for a specific DK contest — see
    optimization/contest_calibration.py. Cash doesn't scale with field
    size (a cash lineup should be safe regardless of how many entries are
    in the contest), so calibration only applies to GPP-style objectives.
    Best-effort: any failure to reach DK's contest listing just means no
    override, never a build failure.
    """
    if not dk_contest_id or objective == "cash":
        return None, None
    from app.data_sources.draftkings import DraftKingsApiSource
    from app.optimization.contest_calibration import contest_calibrated_weights

    try:
        contests = DraftKingsApiSource().get_nfl_contests().data
    except SourceUnavailableError:
        return None, None
    contest = next((c for c in contests if c.dk_contest_id == dk_contest_id), None)
    if not contest:
        return None, None
    weights = contest_calibrated_weights(contest.max_entries)
    note = f"Calibrated to \"{contest.name}\" ({contest.max_entries:,}-entry field)"
    return weights, note


def _optimize_lineups(db, slate, dk_player_rows, ensemble_by_player_id, ownership_by_player_id, sim_result, sim_run, options: BuildOptions, game_env_by_game_id: dict | None = None, correlation_entries=None):
    from app.config.loader import get_optimization_settings

    opt_cfg = get_optimization_settings()
    mode_cfg = opt_cfg["contest_modes"].get(options.objective, opt_cfg["contest_modes"]["large_field_gpp"])
    calibrated_weights, calibration_note = _resolve_contest_calibration(options.objective, options.dk_contest_id)
    weights = calibrated_weights or mode_cfg["objective_weights"]

    correlation_scores = _compute_correlation_scores(
        correlation_entries or [], {r.player_id for r in dk_player_rows if r.player_id}
    )
    # Correlation values live in [-1, 1]; scale up so the weighted term is
    # comparable in magnitude to the point-based terms (median/ceiling
    # etc., which run ~5-30) rather than being numerically negligible.
    CORRELATION_SCALE = 15.0

    optimizer_players = []
    players_by_id = {}
    for r in dk_player_rows:
        info = ensemble_by_player_id.get(r.player_id)
        if not info or not r.game_id:
            continue
        ens = info["ensemble"]
        sim = sim_result.player_summaries.get(r.player_id)
        own = ownership_by_player_id.get(r.player_id)
        own_pct = own.projected_ownership_pct if own else 10.0
        leverage_proxy = max(0.0, (sim.prob_top5pct * 100 if sim else 0) - own_pct)

        # The correlation matrix has no concept of depth-chart status — a
        # 4th-string QB gets the exact same "qb_own_wr" correlation value
        # against his team's receivers as the actual starter (see
        # correlations/engine.py). Every OTHER term here is already
        # implicitly discounted for backups (ens.*/sim.* are computed from
        # a projection that's already scaled by depth_chart_multiplier),
        # so leaving this one term undiscounted let a cheap, correlation-
        # rich backup QB's objective_value get inflated purely from being
        # "connected" to many teammates — invisible in Classic (only 1 QB
        # slot exists) but very visible in Showdown, where nothing stops
        # the optimizer from rostering several backup QBs in open FLEX
        # slots once this term dominates their otherwise-correct discount.
        depth_multiplier = info.get("depth_chart_multiplier", 1.0)

        objective_value = (
            weights.get("median", 0) * ens.median
            + weights.get("projection", 0) * ens.ensemble_projection
            + weights.get("ceiling", 0) * (sim.ceiling if sim else ens.ceiling)
            + weights.get("floor", 0) * ens.floor
            + weights.get("leverage", 0) * leverage_proxy
            + weights.get("correlation", 0) * correlation_scores.get(r.player_id, 0.0) * CORRELATION_SCALE * depth_multiplier
            + weights.get("uniqueness", 0) * 0  # uniqueness is enforced structurally via exposure/overlap constraints in diversification.py, not a per-player scalar
            + weights.get("volatility", 0) * (sim.std_dev if sim else ens.std_dev)
        )

        salary = r.salaries[-1].salary if r.salaries else 0
        op = OptimizerPlayer(
            player_id=r.player_id, position=r.dk_position, team=r.team_abbreviation,
            game_id=r.game_id, salary=salary, objective_value=round(objective_value, 3),
        )
        optimizer_players.append(op)
        players_by_id[r.player_id] = op

    rules = get_contest_rules("nfl", slate.contest_type)
    diversification = DiversificationSettings(**opt_cfg["default_diversification"])

    opt_run = OptimizationRun(
        slate_id=slate.id, simulation_run_id=sim_run.id, objective=options.objective,
        num_lineups_requested=options.num_lineups,
        settings={"weights": weights, "contest_calibration": calibration_note, "dk_contest_id": options.dk_contest_id},
        status="running",
    )
    db.add(opt_run)
    db.flush()

    # GPP modes (correlation weight > 0) get a rotation of forced
    # QB+pass-catcher stacks — spec section 16's stacking engine — cycling
    # through the highest-implied-total teams first so the portfolio leans
    # into the slate's best game environments rather than leaving
    # correlation to emerge (or not) from a linear per-player objective.
    stack_teams: list[str] | None = None
    if weights.get("correlation", 0) > 0 and game_env_by_game_id:
        team_totals: dict[str, float] = {}
        for r in dk_player_rows:
            if r.dk_position != "QB" or not r.game_id:
                continue
            info = ensemble_by_player_id.get(r.player_id)
            if info:
                team_totals[r.team_abbreviation] = info["env"].get(
                    "home_implied_total" if info["is_home"] else "away_implied_total", 22.0
                )
        stack_teams = [t for t, _ in sorted(team_totals.items(), key=lambda kv: kv[1], reverse=True)] or None

    portfolio = generate_portfolio(
        optimizer_players, rules, options.num_lineups, diversification,
        forced_qb_stack_teams=stack_teams,
        randomness_pct=mode_cfg.get("randomness_pct", 0.0), seed=options.seed,
    )

    lineups = []
    for lu in portfolio.lineups:
        stack = classify_stack(lu.assignments, players_by_id)
        ens_projs = [ensemble_by_player_id[a.player_id]["ensemble"] for a in lu.assignments]
        lineup = Lineup(
            optimization_run_id=opt_run.id, slate_id=slate.id, salary_used=lu.salary_used,
            salary_remaining=rules.salary_cap - lu.salary_used,
            projected_points=round(sum(e.ensemble_projection for e in ens_projs), 2),
            ceiling=round(sum(e.ceiling for e in ens_projs), 2),
            floor=round(sum(e.floor for e in ens_projs), 2),
            stack_type=stack.stack_type.value if stack.stack_type else None,
            stack_description=stack.description,
        )
        db.add(lineup)
        db.flush()
        for a in lu.assignments:
            db.add(LineupPlayer(
                lineup_id=lineup.id, player_id=a.player_id, roster_slot=a.slot,
                salary=a.salary, projected_points=a.objective_value,
            ))
        lineups.append(lineup)

    opt_run.num_lineups_generated = len(lineups)
    opt_run.status = "completed"
    opt_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
    return opt_run, lineups, calibration_note
