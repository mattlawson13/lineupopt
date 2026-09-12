"""Betting-lines adapter — The Odds API (free tier, REQUIRES an API key).

We do not scrape sportsbooks directly (ToS-restricted). If ODDS_API_KEY is
unset, `get_nfl_lines()` raises SourceUnavailableError immediately with a
clear message so the ingestion layer falls back to manual import — it never
silently returns fabricated lines.
"""
from __future__ import annotations

import dataclasses

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.data_sources.http_client import ThrottledClient
from app.models.enums import SourceConfidence
from app.settings import get_settings


@dataclasses.dataclass
class GameLine:
    home_team: str
    away_team: str
    commence_time_utc: str
    spread_home: float | None
    total: float | None
    moneyline_home: int | None
    moneyline_away: int | None
    book: str


class BettingSource(DataSource):
    name = "the_odds_api"
    default_confidence = SourceConfidence.HIGH

    def __init__(self, client: ThrottledClient | None = None):
        self._settings = get_settings()
        self._client = client or ThrottledClient(min_interval_seconds=1.0)

    def get_nfl_lines(self, preferred_book: str = "draftkings") -> SourceResult[list[GameLine]]:
        if not self._settings.odds_api_key:
            raise SourceUnavailableError(
                "ODDS_API_KEY not configured — sign up at https://the-odds-api.com/ and set it in .env, "
                "or import betting lines manually via POST /api/betting/import"
            )

        url = f"{self._settings.odds_api_base_url}/sports/americanfootball_nfl/odds"
        params = {
            "apiKey": self._settings.odds_api_key,
            "regions": "us",
            "markets": "spreads,totals,h2h",
            "oddsFormat": "american",
        }
        try:
            payload, from_cache = self._client.get_json(url, params=params, cache_ttl_seconds=900)
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_nfl_lines", exc)
            raise SourceUnavailableError(f"The Odds API unreachable: {exc}") from exc

        lines: list[GameLine] = []
        warnings: list[str] = []
        for game in payload:
            try:
                book_data = next(
                    (b for b in game.get("bookmakers", []) if b.get("key") == preferred_book),
                    game.get("bookmakers", [{}])[0] if game.get("bookmakers") else None,
                )
                if not book_data:
                    warnings.append(f"No bookmaker data for {game.get('home_team')} vs {game.get('away_team')}")
                    continue

                spread_home = total = ml_home = ml_away = None
                for market in book_data.get("markets", []):
                    if market["key"] == "spreads":
                        for outcome in market["outcomes"]:
                            if outcome["name"] == game["home_team"]:
                                spread_home = outcome["point"]
                    elif market["key"] == "totals":
                        total = market["outcomes"][0]["point"] if market["outcomes"] else None
                    elif market["key"] == "h2h":
                        for outcome in market["outcomes"]:
                            if outcome["name"] == game["home_team"]:
                                ml_home = outcome["price"]
                            elif outcome["name"] == game["away_team"]:
                                ml_away = outcome["price"]

                lines.append(
                    GameLine(
                        home_team=game["home_team"],
                        away_team=game["away_team"],
                        commence_time_utc=game["commence_time"],
                        spread_home=spread_home,
                        total=total,
                        moneyline_home=ml_home,
                        moneyline_away=ml_away,
                        book=book_data.get("key", "unknown"),
                    )
                )
            except (KeyError, IndexError, TypeError) as exc:
                warnings.append(f"Skipped malformed odds row: {exc}")

        return self._result(lines, warnings=warnings, raw_meta={"from_cache": from_cache})
