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

import dataclasses
import datetime
import logging
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.correlations.engine import CorrelationEntry, CorrelationPlayer, build_correlation_matrix
from app.data_sources.base import SourceUnavailableError
from app.data_sources.betting import BettingSource
from app.data_sources.draftkings import DraftKingsApiSource, DraftKingsCsvSource, DraftKingsSlate
from app.data_sources.injury import InjurySource
from app.data_sources.nfl_stats import NFLStatsSource
from app.data_sources.weather import WeatherSource
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
    db.flush()
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
    injury_by_norm_name, injury_warning = _fetch_injuries(team_by_abbrev.keys())
    if injury_warning:
        yield BuildProgressEvent("injuries", "warning", injury_warning)
    else:
        yield BuildProgressEvent("injuries", "success", f"{len(injury_by_norm_name)} injury designations loaded")

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
        injury_by_norm_name, game_logs, matchup_zscores, model_version,
    )
    yield BuildProgressEvent("projections", "success", f"Ensemble projections built for {len(ensemble_by_player_id)} players")

    # ---- 8. Ownership --------------------------------------------------------
    yield BuildProgressEvent("ownership", "running", "Projecting ownership...")
    ownership_by_player_id = _build_ownership(db, slate, dk_player_rows, ensemble_by_player_id, game_env_by_game_id, injury_by_norm_name)
    yield BuildProgressEvent("ownership", "success", f"Ownership projected for {len(ownership_by_player_id)} players")

    # ---- 9. Correlations -------------------------------------------------
    yield BuildProgressEvent("correlations", "running", "Building correlation matrix...")
    correlation_entries = _build_correlations(db, slate, dk_player_rows, game_env_by_game_id)
    yield BuildProgressEvent("correlations", "success", f"{len(correlation_entries)} pairwise correlations computed")

    # ---- 10. Monte Carlo simulation ---------------------------------------
    yield BuildProgressEvent("simulation", "running", f"Running {options.num_simulations:,} simulations...")
    sim_result, sim_run = _run_simulation(
        db, slate, dk_player_rows, ensemble_by_player_id, game_by_teams, game_env_by_game_id, correlation_entries, options
    )
    yield BuildProgressEvent(
        "simulation", "success",
        f"{sim_result.num_simulations:,} simulations completed in {sim_result.duration_seconds}s",
    )

    # ---- 11. Optimize lineups ------------------------------------------------
    yield BuildProgressEvent("optimize", "running", f"Generating {options.num_lineups} lineups...")
    opt_run, lineups = _optimize_lineups(
        db, slate, dk_player_rows, ensemble_by_player_id, ownership_by_player_id, sim_result, sim_run, options,
        game_env_by_game_id,
    )
    yield BuildProgressEvent("optimize", "success", f"{len(lineups)} lineups generated")

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
            week=_estimate_nfl_week(earliest), start_time_utc=earliest, game_ids=[],
            source="csv_import" if not dk_slate.players[0].dk_draftable_id else "dk_api",
            imported_at=now,
        )
        db.add(slate)
        db.flush()

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


def _fetch_and_apply_betting_lines(db: Session, game_by_teams: dict) -> tuple[dict, str | None]:
    game_env: dict[str, dict] = {}
    try:
        source = BettingSource()
        result = source.get_nfl_lines()
        by_teams = {}
        for line in result.data:
            by_teams[(line.home_team, line.away_team)] = line

        for key, game in game_by_teams.items():
            match = next((line for (h, a), line in by_teams.items() if game.home_team.abbreviation in h or game.away_team.abbreviation in a), None)
            if match and match.spread_home is not None and match.total is not None:
                env = compute_game_environment(match.spread_home, match.total)
                game_env[game.id] = dataclasses.asdict(env)
                db.add(__import__("app.models.context_data", fromlist=["BettingLine"]).BettingLine(
                    game_id=game.id, book=match.book, spread_home=match.spread_home, total=match.total,
                    implied_total_home=env.home_implied_total, implied_total_away=env.away_implied_total,
                ))
            else:
                game_env[game.id] = dataclasses.asdict(compute_game_environment(0.0, 44.0))
        return game_env, None
    except SourceUnavailableError as exc:
        for game in game_by_teams.values():
            game_env[game.id] = dataclasses.asdict(compute_game_environment(0.0, 44.0))
        return game_env, f"{exc} — using neutral (0 spread / 44 total) defaults"


def _fetch_and_apply_weather(db: Session, game_by_teams: dict) -> list[str]:
    warnings: list[str] = []
    source = WeatherSource()
    from app.models.context_data import Weather

    for game in game_by_teams.values():
        home_abbrev = game.home_team.abbreviation
        is_dome = home_abbrev in DOME_TEAMS
        coords = TEAM_STADIUM_COORDS.get(home_abbrev)
        if not coords:
            warnings.append(f"No stadium coordinates for {home_abbrev}")
            continue
        try:
            result = source.get_forecast_for_kickoff(coords[0], coords[1], game.kickoff_utc, is_dome=is_dome)
            f = result.data
            db.add(Weather(
                game_id=game.id, temperature_f=f.temperature_f, wind_mph=f.wind_mph,
                wind_direction_deg=f.wind_direction_deg, precipitation_pct=f.precipitation_pct,
                precipitation_type=f.precipitation_type, humidity_pct=f.humidity_pct, is_forecast=True,
            ))
        except SourceUnavailableError as exc:
            warnings.append(str(exc))
    return warnings


def _fetch_injuries(team_abbrevs) -> tuple[dict, str | None]:
    source = InjurySource()
    out: dict[str, str] = {}
    failures = 0
    for abbrev in team_abbrevs:
        try:
            result = source.get_team_injuries(abbrev)
            for row in result.data:
                out[normalize_name(row.player_name)] = row.status
        except SourceUnavailableError:
            failures += 1
    warning = None
    if failures == len(list(team_abbrevs)) and failures > 0:
        warning = "ESPN injury endpoint unreachable from this environment — assuming all players healthy; import a report via POST /api/injuries/import"
    return out, warning


def _load_historical_stats(season: int, week: int) -> tuple[dict, dict, str | None]:
    try:
        source = NFLStatsSource()
        df = source.get_player_stats().data
        lookup_season = season if (df["season"] == season).any() else int(df["season"].max())
        game_logs = build_game_logs_by_player(df, lookup_season, through_week=week if lookup_season == season else None)
        fpts_allowed = build_fpts_allowed_by_team_position(df, lookup_season, through_week=week if lookup_season == season else None)
        warning = None if lookup_season == season else f"No {season} data yet — using {lookup_season} historical rates"
        return game_logs, fpts_allowed, warning
    except SourceUnavailableError as exc:
        return {}, {}, f"{exc} — projections will fall back to positional priors"


def _build_projections(
    db, slate, dk_player_rows, team_by_abbrev, game_by_teams, game_env_by_game_id,
    injury_by_norm_name, game_logs, matchup_zscores, model_version,
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

        key = (normalize_name(dk_row.display_name), position)
        logs = game_logs.get(key, [])
        season_usage = compute_usage_snapshot(logs)
        recent_usage = compute_usage_snapshot(logs[-3:]) if len(logs) >= 1 else {}

        norm_name = normalize_name(dk_row.display_name)
        injury_status = injury_by_norm_name.get(norm_name, "healthy")
        availability = {"healthy": 1.0, "questionable": 0.9, "doubtful": 0.4, "out": 0.0, "ir": 0.0}.get(injury_status, 1.0)

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

        market = market_projection_from_vegas(position, ctx.implied_team_total, ctx.opponent_implied_total)
        sources = SourceProjections(model_projection=component.projected_points, model_std_dev=component.model_uncertainty, market_projection=market)
        ensemble = compute_ensemble(sources, position)

        db.add(Projection(
            slate_id=slate.id, player_id=player.id, dk_player_id_fk=dk_row.id, source_id=proj_source_model.id,
            model_version_id=model_version.id, projected_points=component.projected_points,
            floor=component.floor, median=component.projected_points, ceiling=component.ceiling,
            std_dev=component.model_uncertainty, confidence=0.85, inputs={"season_usage": season_usage, "recent_usage": recent_usage},
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
            "env": env, "injury_status": injury_status,
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


def _optimize_lineups(db, slate, dk_player_rows, ensemble_by_player_id, ownership_by_player_id, sim_result, sim_run, options: BuildOptions, game_env_by_game_id: dict | None = None):
    from app.config.loader import get_optimization_settings

    opt_cfg = get_optimization_settings()
    mode_cfg = opt_cfg["contest_modes"].get(options.objective, opt_cfg["contest_modes"]["large_field_gpp"])
    weights = mode_cfg["objective_weights"]

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

        objective_value = (
            weights.get("median", 0) * ens.median
            + weights.get("projection", 0) * ens.ensemble_projection
            + weights.get("ceiling", 0) * (sim.ceiling if sim else ens.ceiling)
            + weights.get("floor", 0) * ens.floor
            + weights.get("leverage", 0) * leverage_proxy
            + weights.get("correlation", 0) * 0  # realized via forced stacks / covariance in simulation, not a per-player scalar
            + weights.get("uniqueness", 0) * 0
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
        num_lineups_requested=options.num_lineups, settings={"weights": weights}, status="running",
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
    return opt_run, lineups
