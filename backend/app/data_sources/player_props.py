"""Player-prop odds adapter — The Odds API's per-event odds endpoint (same
provider/key as betting.py's game lines, REQUIRES an API key).

Unlike get_nfl_lines() (one call covers the whole week's spreads/totals),
player props are priced per-event and billed per-market: verified live
against a real NFL event that requesting 5 markets in one call costs 5
quota units (`x-requests-remaining` dropped by exactly 5), confirmed via
the `x-requests-*` response headers The Odds API returns on every call.
On the free tier (~500/month) that means a single full 13-game Sunday
slate would cost ~65 units to prop up — real money against a small
monthly budget — so callers MUST scope this to only the games on the
slate being built (never a whole week) and MUST cache aggressively. This
adapter does not do that scoping itself; see features/player_props.py for
the per-slate orchestration and the long cache TTLs used here.

Markets verified reachable 2026-09-14 against a live NFL event:
player_pass_yds, player_rush_yds, player_receptions, player_reception_yds,
player_anytime_td. Other NFL player markets The Odds API documents
(player_pass_tds, player_pass_interceptions, ...) were NOT verified live
and are deliberately not requested here — per project policy we don't
wire up an endpoint/market we haven't confirmed actually returns data on
this account's plan tier.
"""
from __future__ import annotations

import dataclasses

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.data_sources.http_client import ThrottledClient
from app.models.enums import SourceConfidence
from app.settings import get_settings

NFL_PLAYER_PROP_MARKETS = [
    "player_pass_yds",
    "player_rush_yds",
    "player_receptions",
    "player_reception_yds",
    "player_anytime_td",
]


@dataclasses.dataclass
class PropEvent:
    event_id: str
    home_team: str  # full team name, e.g. "Kansas City Chiefs" — see sports/nfl/team_names.py
    away_team: str
    commence_time_utc: str


@dataclasses.dataclass
class PlayerPropOutcome:
    player_name: str
    market: str  # one of NFL_PLAYER_PROP_MARKETS
    side: str  # "Over" | "Under" | "Yes"
    point: float | None  # None for player_anytime_td (a yes/no line has no point)
    price: int  # American odds


class PlayerPropsSource(DataSource):
    name = "the_odds_api"
    default_confidence = SourceConfidence.HIGH

    def __init__(self, client: ThrottledClient | None = None):
        self._settings = get_settings()
        self._client = client or ThrottledClient(min_interval_seconds=1.0)

    def get_nfl_events(self) -> SourceResult[list[PropEvent]]:
        if not self._settings.odds_api_key:
            raise SourceUnavailableError("ODDS_API_KEY not configured — cannot fetch player props")

        url = f"{self._settings.odds_api_base_url}/sports/americanfootball_nfl/events"
        try:
            payload, from_cache = self._client.get_json(
                url, params={"apiKey": self._settings.odds_api_key}, cache_ttl_seconds=1800
            )
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_nfl_events", exc)
            raise SourceUnavailableError(f"The Odds API events endpoint unreachable: {exc}") from exc

        events = [
            PropEvent(
                event_id=e["id"], home_team=e["home_team"], away_team=e["away_team"],
                commence_time_utc=e["commence_time"],
            )
            for e in payload
        ]
        return self._result(events, raw_meta={"from_cache": from_cache})

    def get_event_player_props(
        self, event_id: str, preferred_book: str = "draftkings"
    ) -> SourceResult[list[PlayerPropOutcome]]:
        """One event's player props across NFL_PLAYER_PROP_MARKETS — costs
        len(NFL_PLAYER_PROP_MARKETS) quota units per call (see module
        docstring), cached for 3 hours since prop lines don't need
        minute-level freshness for pregame lineup building and slates get
        rebuilt repeatedly during a single build session.
        """
        if not self._settings.odds_api_key:
            raise SourceUnavailableError("ODDS_API_KEY not configured — cannot fetch player props")

        url = f"{self._settings.odds_api_base_url}/sports/americanfootball_nfl/events/{event_id}/odds"
        params = {
            "apiKey": self._settings.odds_api_key,
            "regions": "us",
            "markets": ",".join(NFL_PLAYER_PROP_MARKETS),
            "oddsFormat": "american",
        }
        try:
            payload, from_cache = self._client.get_json(url, params=params, cache_ttl_seconds=10800)
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_event_player_props", exc)
            raise SourceUnavailableError(f"The Odds API event-odds endpoint unreachable for {event_id}: {exc}") from exc

        bookmakers = payload.get("bookmakers", [])
        book = next((b for b in bookmakers if b.get("key") == preferred_book), bookmakers[0] if bookmakers else None)
        if not book:
            return self._result([], warnings=[f"No bookmaker offered player props for event {event_id}"], raw_meta={"from_cache": from_cache})

        outcomes: list[PlayerPropOutcome] = []
        warnings: list[str] = []
        for market in book.get("markets", []):
            for outcome in market.get("outcomes", []):
                try:
                    outcomes.append(
                        PlayerPropOutcome(
                            player_name=outcome["description"],
                            market=market["key"],
                            side=outcome["name"],
                            point=outcome.get("point"),
                            price=outcome["price"],
                        )
                    )
                except KeyError as exc:
                    warnings.append(f"Skipped malformed prop outcome: {exc}")

        return self._result(outcomes, warnings=warnings, raw_meta={"from_cache": from_cache, "book": book.get("key")})
