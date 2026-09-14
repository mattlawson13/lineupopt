"""ESPN's "core" API (sports.core.api.espn.com) as a same-season
supplement to nflverse (app/data_sources/nfl_stats.py).

Why this exists: nflverse's player_stats pipeline has been stale since
before the 2025 season started (verified: player_stats_2025.csv 404s, and
the combined player_stats.csv's Last-Modified header predates week 1) —
_load_historical_stats() in ingestion/slate_builder.py falls back to
last-year's rates whenever this happens. This module fills that specific
gap with real in-season box scores, once they exist, without replacing
nflverse as the source of multi-season historical priors.

Two ESPN subdomains behave very differently in this environment:
site.api.espn.com (used elsewhere for injury reports) is 403-blocked;
sports.core.api.espn.com is not, and is unauthenticated/undocumented but
stable (it's what ESPN's own site and app run on, and is the basis of
several long-running open-source clients, e.g. the `espn-api` package).
It's not a licensed feed like SportsDataIO/Sportradar, so treat it as a
best-effort supplement, not a primary source: any failure here should
degrade gracefully back to whatever nflverse already provided, never
break a slate build.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from pathlib import Path

import httpx

from app.normalization.player_matcher import normalize_name, normalize_team_abbreviation
from app.projections.nfl.dk_points import compute_dk_points

BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

# fetch_season_rows() re-walks every completed week on every call (it has
# no notion of "already fetched this"), and a real slate build calls
# _load_historical_stats() every time it runs — without caching, a
# week-10 build would redo a 9-week, ~5000-request backfill on every
# single build. Box scores for a *completed* week never change, so a
# whole-week result is cached to disk keyed by (season, week, season_type)
# with a long-but-not-infinite TTL (in case ESPN issues a late correction).
_CACHE_DIR = Path(__file__).parent.parent.parent / ".cache" / "espn"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_WEEK_CACHE_TTL_SECONDS = 6 * 3600

# Stable across seasons (ESPN's team taxonomy, not season data) — verified
# live against seasons/2025/teams.
TEAM_ID_TO_ABBREV = {
    "22": "ARI", "1": "ATL", "33": "BAL", "2": "BUF", "29": "CAR", "3": "CHI",
    "4": "CIN", "5": "CLE", "6": "DAL", "7": "DEN", "8": "DET", "9": "GB",
    "34": "HOU", "11": "IND", "30": "JAX", "12": "KC", "24": "LAC", "14": "LAR",
    "13": "LV", "15": "MIA", "16": "MIN", "17": "NE", "18": "NO", "19": "NYG",
    "20": "NYJ", "21": "PHI", "23": "PIT", "26": "SEA", "25": "SF", "27": "TB",
    "10": "TEN", "28": "WSH",
}
ABBREV_TO_TEAM_ID = {v: k for k, v in TEAM_ID_TO_ABBREV.items()}

# Also stable (ESPN's position taxonomy). Folds FB/HB into RB to match
# NFL_POSITION_MAP in features/nflverse_adapter.py, so keys from this
# module and from nflverse line up for merging.
POSITION_ID_TO_GROUP = {"1": "WR", "7": "TE", "8": "QB", "9": "RB", "10": "RB", "101": "RB"}

_TIMEOUT = 15.0
_REF_ID_RE = re.compile(r"/(\d+)(?:\?.*)?$")

# Roster entries only carry a short displayName ("Barkley"), not a full
# name that would match nflverse's player_display_name ("Saquon Barkley")
# for the merge in build_game_logs_by_player — the athlete resource has
# the full name, so it needs its own fetch per player. Athlete IDs are
# stable for a player's whole career, so caching here (not just within one
# week's fetch) avoids re-fetching the same ~300 skill players every week
# of a multi-week fetch_season_rows() call.
_athlete_name_cache: dict[str, str] = {}


class ESPNUnavailableError(RuntimeError):
    """Raised only when the whole week is unreachable (e.g. no network) —
    a single game or player failing just gets skipped, since box scores
    for the other games/players are still real, useful, independent data.
    """


def _ref_id(ref: dict | str | None) -> str:
    if not ref:
        return ""
    url = ref["$ref"] if isinstance(ref, dict) else ref
    m = _REF_ID_RE.search(url.split("?")[0])
    return m.group(1) if m else ""


def _get(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    resp = client.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _resolve_athlete_name(client: httpx.Client, athlete_ref: str) -> str:
    """Full name for an athlete ref, via the module-level cache shared by
    box-score and injury fetching alike (see _athlete_name_cache above).
    """
    athlete_id = _ref_id(athlete_ref)
    name = _athlete_name_cache.get(athlete_id)
    if name is None:
        try:
            athlete = _get(client, athlete_ref)
            name = athlete.get("displayName") or athlete.get("fullName") or ""
        except httpx.HTTPError:
            name = ""
        _athlete_name_cache[athlete_id] = name
    return name


def _stat_value(categories: list[dict], category: str, stat: str) -> float:
    for cat in categories:
        if cat["name"] == category:
            for s in cat["stats"]:
                if s["name"] == stat:
                    return float(s.get("value") or 0.0)
    return 0.0


def _espn_stats_to_game_stats(splits: dict, team: str, opponent: str, week: int, season: int) -> dict:
    """Maps ESPN's per-athlete-per-game stat categories onto the same
    keys nflverse_row_to_game_stats() produces (features/nflverse_adapter.py)
    so both sources can share build_game_logs_by_player-style downstream
    code and merge cleanly.
    """
    cats = splits["categories"]
    stats = {
        "pass_attempts": _stat_value(cats, "passing", "passingAttempts"),
        "pass_completions": _stat_value(cats, "passing", "completions"),
        "pass_yards": _stat_value(cats, "passing", "passingYards"),
        "pass_td": _stat_value(cats, "passing", "passingTouchdowns"),
        "interceptions": _stat_value(cats, "passing", "interceptions"),
        "rush_attempts": _stat_value(cats, "rushing", "rushingAttempts"),
        "rush_yards": _stat_value(cats, "rushing", "rushingYards"),
        "rush_td": _stat_value(cats, "rushing", "rushingTouchdowns"),
        "targets": _stat_value(cats, "receiving", "receivingTargets"),
        "receptions": _stat_value(cats, "receiving", "receptions"),
        "rec_yards": _stat_value(cats, "receiving", "receivingYards"),
        "rec_td": _stat_value(cats, "receiving", "receivingTouchdowns"),
        "fumbles_lost": _stat_value(cats, "general", "fumblesLost"),
    }
    # DK points, not nflverse's own PPR column — this app scores
    # everything in DK points already (dk_points.py), and the value is
    # only ever used for a same-week relative z-score (features/matchup.py),
    # so reusing the app's own scoring is more consistent than reimplementing
    # a separate PPR formula here.
    stats["fantasy_points_ppr"] = round(compute_dk_points(stats), 2)
    stats["opponent_team"] = normalize_team_abbreviation(opponent)
    stats["team"] = normalize_team_abbreviation(team)
    stats["week"] = week
    stats["season"] = season
    return stats


def fetch_week_player_rows(season: int, week: int, season_type: int = 2) -> list[dict]:
    """Cached wrapper around _fetch_week_player_rows_uncached — see
    _CACHE_DIR comment above for why this matters. A cache hit costs one
    disk read instead of the ~15-40 HTTP requests one week takes.
    """
    cache_file = _CACHE_DIR / f"{season}_{week}_{season_type}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < _WEEK_CACHE_TTL_SECONDS:
        return json.loads(cache_file.read_text())

    rows = _fetch_week_player_rows_uncached(season, week, season_type)
    cache_file.write_text(json.dumps(rows))
    return rows


def _fetch_week_player_rows_uncached(season: int, week: int, season_type: int = 2) -> list[dict]:
    """Every skill-position player's real box score for one completed
    week. Each row also carries `player_display_name` and `position` (WR/
    TE/QB/RB) alongside the nflverse-shaped stat keys above — pass the
    list to build_game_logs_by_player()/build_fpts_allowed_by_team_position()
    below. Raises ESPNUnavailableError only if the week's schedule itself
    can't be fetched; a single game or player failing is skipped silently
    (partial real data beats none).
    """
    with httpx.Client(follow_redirects=True) as client:
        try:
            events = _get(
                client,
                f"{BASE}/seasons/{season}/types/{season_type}/weeks/{week}/events",
                params={"limit": 32},
            )
        except httpx.HTTPError as exc:
            raise ESPNUnavailableError(f"ESPN core API unreachable for {season} week {week}: {exc}") from exc

        event_ids = [_ref_id(item) for item in events.get("items", [])]
        if not event_ids:
            return []

        # Phase 1: cheap per-game metadata (competitors + rosters) to find
        # which skill-position players actually have a box score worth
        # fetching, before spending a request on each one.
        pending: list[dict] = []
        for event_id in event_ids:
            try:
                comp = _get(client, f"{BASE}/events/{event_id}/competitions/{event_id}")
            except httpx.HTTPError:
                continue
            competitors = comp.get("competitors", [])
            team_abbrevs = {c["id"]: TEAM_ID_TO_ABBREV.get(_ref_id(c.get("team")), "") for c in competitors}
            for c in competitors:
                own = team_abbrevs.get(c["id"], "")
                opponent = next((v for k, v in team_abbrevs.items() if k != c["id"]), "")
                roster_ref = c.get("roster", {}).get("$ref")
                if not own or not roster_ref:
                    continue
                try:
                    roster = _get(client, roster_ref)
                except httpx.HTTPError:
                    continue
                for entry in roster.get("entries", []):
                    if entry.get("didNotPlay"):
                        continue
                    position = POSITION_ID_TO_GROUP.get(_ref_id(entry.get("position")))
                    stats_ref = entry.get("statistics", {}).get("$ref")
                    athlete_ref = entry.get("athlete", {}).get("$ref")
                    if not position or not stats_ref or not athlete_ref:
                        continue
                    pending.append({
                        "ref": stats_ref, "team": own, "opponent": opponent,
                        "position": position, "athlete_ref": athlete_ref,
                    })

        if not pending:
            return []

        # Phase 2: each player's box score is an independent HTTP call —
        # same concurrency pattern as _fetch_and_apply_weather in
        # ingestion/slate_builder.py.
        def fetch_one(item: dict) -> dict | None:
            try:
                doc = _get(client, item["ref"])
            except httpx.HTTPError:
                return None
            game_stats = _espn_stats_to_game_stats(doc["splits"], item["team"], item["opponent"], week, season)
            if not game_stats["pass_attempts"] and not game_stats["rush_attempts"] and not game_stats["targets"]:
                return None  # listed but didn't meaningfully play (e.g. inactive)

            name = _resolve_athlete_name(client, item["athlete_ref"])
            if not name:
                return None  # can't safely key this player for merging

            game_stats["player_display_name"] = name
            game_stats["position"] = item["position"]
            return game_stats

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(pending))) as pool:
            results = list(pool.map(fetch_one, pending))

    return [r for r in results if r is not None]


def fetch_season_rows(season: int, through_week: int, season_type: int = 2) -> list[dict]:
    """All completed weeks (1..through_week-1) of `season`, oldest week
    first. Stops (keeping whatever it already has) at the first week whose
    schedule itself can't be fetched, rather than failing the whole call —
    partial real in-season data is still strictly better than none.
    """
    rows: list[dict] = []
    for wk in range(1, through_week):
        try:
            rows.extend(fetch_week_player_rows(season, wk, season_type))
        except ESPNUnavailableError:
            break
    return rows


# ESPN's injury `type.name` values -> this app's vocabulary (matches
# InjuryStatus/the "healthy"|"questionable"|"doubtful"|"out"|"ir" set
# already used throughout ingestion/slate_builder.py). ACTIVE means "no
# current designation" (i.e. healthy) and is deliberately unmapped so
# those entries get skipped — see fetch_all_injuries.
INJURY_STATUS_MAP = {
    "INJURY_STATUS_QUESTIONABLE": "questionable",
    "INJURY_STATUS_DOUBTFUL": "doubtful",
    "INJURY_STATUS_OUT": "out",
    "INJURY_STATUS_IR": "ir",
    "INJURY_STATUS_INJURED_RESERVE": "ir",
    "INJURY_STATUS_SUSPENSION": "out",  # not an injury, but equally unavailable this week
}


def fetch_all_injuries(team_abbrevs: list[str]) -> dict[str, str]:
    """{normalized_player_name: status} for every player league-wide who
    currently carries a real injury designation, from ESPN's core API —
    reachable from this environment, unlike site.api.espn.com (the host
    data_sources/injury.py uses, 403-blocked here). Same underlying ESPN
    injury data, different host.

    Each team's injuries list actually returns *every* player with injury
    history, most now resolved back to "Active" — those are skipped, since
    "no current designation" already means healthy under this app's
    default. Unrecognized/new status names are skipped the same way
    (silently treating them as "no real signal") rather than guessed at.

    Best-effort like the rest of this module: a team or player that fails
    to fetch is just missing from the result, never raises.
    """
    with httpx.Client(follow_redirects=True) as client:
        def list_team(abbrev: str) -> list[str]:
            team_id = ABBREV_TO_TEAM_ID.get(normalize_team_abbreviation(abbrev))
            if not team_id:
                return []
            try:
                doc = _get(client, f"{BASE}/teams/{team_id}/injuries", params={"limit": 50})
            except httpx.HTTPError:
                return []
            return [item["$ref"] for item in doc.get("items", [])]

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(team_abbrevs)) or 1) as pool:
            refs = [ref for team_refs in pool.map(list_team, team_abbrevs) for ref in team_refs]

        if not refs:
            return {}

        def fetch_one(ref: str) -> tuple[str, str] | None:
            try:
                doc = _get(client, ref)
            except httpx.HTTPError:
                return None
            status = INJURY_STATUS_MAP.get(doc.get("type", {}).get("name", ""))
            if not status:
                return None  # Active or unrecognized -> no current designation
            athlete_ref = doc.get("athlete", {}).get("$ref")
            if not athlete_ref:
                return None
            name = _resolve_athlete_name(client, athlete_ref)
            if not name:
                return None
            return normalize_name(name), status

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(refs))) as pool:
            results = list(pool.map(fetch_one, refs))

    return {name: status for r in results if r is not None for name, status in [r]}


def build_game_logs_by_player(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Same shape as nflverse_adapter.build_game_logs_by_player: {(name,
    position): [game_stats, ...]}, oldest-first (relies on `rows` already
    being in week order, as fetch_season_rows returns it).
    """
    logs: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (normalize_name(row["player_display_name"]), row["position"])
        stats = {k: v for k, v in row.items() if k not in ("player_display_name", "position")}
        logs.setdefault(key, []).append(stats)
    return logs


def build_fpts_allowed_by_team_position(rows: list[dict]) -> dict[tuple[str, str], float]:
    """Same shape/meaning as nflverse_adapter.build_fpts_allowed_by_team_position
    — the direct input to features/matchup.py's z-score computation — but
    from this season's real games instead of last season's.
    """
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        grouped.setdefault((row["opponent_team"], row["position"]), []).append(row["fantasy_points_ppr"])
    return {key: round(sum(vals) / len(vals), 2) for key, vals in grouped.items()}


def _espn_team_stats_to_dst_stats(splits: dict, team: str, week: int, season: int) -> dict:
    """Maps ESPN's per-team-per-game stat categories onto the raw count
    fields projections/nfl/dst.py's _stat_line() expects (via
    features/usage_features.py's COUNT_FIELDS averaging) — nflverse's
    player_stats has never covered team defense at all (offense-only), so
    unlike the skill-position merge above this isn't filling a staleness
    gap, it's the first time DST has ever had real per-game input instead
    of a hardcoded league-average constant.
    """
    cats = splits["categories"]
    return {
        "sacks": _stat_value(cats, "defensive", "sacks"),
        "interceptions": _stat_value(cats, "defensiveInterceptions", "interceptions"),
        "fumble_recoveries": _stat_value(cats, "returning", "oppFumbleRecoveries"),
        "def_td": _stat_value(cats, "defensive", "defensiveTouchdowns"),
        "safety": _stat_value(cats, "defensive", "safeties"),
        "blocked_kick": _stat_value(cats, "defensive", "kicksBlocked"),
        "return_td": (
            _stat_value(cats, "returning", "kickReturnTouchdowns")
            + _stat_value(cats, "returning", "puntReturnTouchdowns")
        ),
        "points_allowed": _stat_value(cats, "defensive", "pointsAllowed"),
        "team": normalize_team_abbreviation(team),
        "week": week,
        "season": season,
    }


def fetch_week_team_defense_rows(season: int, week: int, season_type: int = 2) -> list[dict]:
    """Cached (see fetch_week_player_rows) real team-level defensive box
    score for every game in one completed week.
    """
    cache_file = _CACHE_DIR / f"{season}_{week}_{season_type}_dst.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < _WEEK_CACHE_TTL_SECONDS:
        return json.loads(cache_file.read_text())

    with httpx.Client(follow_redirects=True) as client:
        try:
            events = _get(
                client,
                f"{BASE}/seasons/{season}/types/{season_type}/weeks/{week}/events",
                params={"limit": 32},
            )
        except httpx.HTTPError as exc:
            raise ESPNUnavailableError(f"ESPN core API unreachable for {season} week {week}: {exc}") from exc

        event_ids = [_ref_id(item) for item in events.get("items", [])]
        if not event_ids:
            return []

        pending: list[dict] = []
        for event_id in event_ids:
            try:
                comp = _get(client, f"{BASE}/events/{event_id}/competitions/{event_id}")
            except httpx.HTTPError:
                continue
            for c in comp.get("competitors", []):
                own = TEAM_ID_TO_ABBREV.get(_ref_id(c.get("team")), "")
                stats_ref = c.get("statistics", {}).get("$ref")
                if not own or not stats_ref:
                    continue
                pending.append({"ref": stats_ref, "team": own})

        def fetch_one(item: dict) -> dict | None:
            try:
                doc = _get(client, item["ref"])
            except httpx.HTTPError:
                return None
            return _espn_team_stats_to_dst_stats(doc["splits"], item["team"], week, season)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(pending)) or 1) as pool:
            results = list(pool.map(fetch_one, pending))

    rows = [r for r in results if r is not None]
    cache_file.write_text(json.dumps(rows))
    return rows


def fetch_season_defense_rows(season: int, through_week: int, season_type: int = 2) -> list[dict]:
    """All completed weeks (1..through_week-1) of team-defense rows,
    oldest week first. Same stop-on-first-failure behavior as
    fetch_season_rows above.
    """
    rows: list[dict] = []
    for wk in range(1, through_week):
        try:
            rows.extend(fetch_week_team_defense_rows(season, wk, season_type))
        except ESPNUnavailableError:
            break
    return rows


def build_dst_game_logs_by_team(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """{(team_abbreviation, "DST"): [game_stats, ...]}, oldest-first —
    keyed by team abbreviation, not a display name, since that's what
    ingestion/slate_builder.py already has on hand for a DST DK row
    (dk_row.team_abbreviation) without needing to guess how DK spells a
    given team's DST entry (e.g. "Eagles" vs "Philadelphia Eagles").
    """
    logs: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (row["team"], "DST")
        stats = {k: v for k, v in row.items() if k != "team"}
        logs.setdefault(key, []).append(stats)
    return logs
