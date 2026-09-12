"""Beat-reporter / news adapter.

Real-time beat-reporter news (Rotoworld/RotoWire wire, Twitter/X breaking
news) is paywalled or requires a paid API in essentially every legitimate
form. We do not scrape it. This adapter:

  1. Best-effort pulls ESPN's public news feed (same host/pattern as
     injury.py) — when reachable, it's free and no-key.
  2. Otherwise supports manual import of a news item (e.g. copy-pasted from
     a beat reporter's public post) via POST /api/news/import, which is
     always available regardless of network access.
"""
from __future__ import annotations

import dataclasses
import datetime

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.data_sources.http_client import ThrottledClient
from app.models.enums import SourceConfidence
from app.settings import get_settings


@dataclasses.dataclass
class NewsItem:
    player_name: str
    headline: str
    body: str | None
    published_at: datetime.datetime
    source: str
    source_url: str | None
    impact: str | None = None  # positive | negative | neutral, set by caller/AI layer, never fabricated here


class NewsSource(DataSource):
    name = "espn_news"
    default_confidence = SourceConfidence.LOW

    def __init__(self, client: ThrottledClient | None = None):
        self._settings = get_settings()
        self._client = client or ThrottledClient(
            min_interval_seconds=1.0,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )

    def get_team_news(self, espn_team_abbreviation: str) -> SourceResult[list[NewsItem]]:
        url = f"{self._settings.espn_api_base_url}/teams/{espn_team_abbreviation.lower()}/news"
        try:
            payload, from_cache = self._client.get_json(url, cache_ttl_seconds=600)
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_team_news", exc)
            raise SourceUnavailableError(
                f"ESPN news endpoint unreachable for {espn_team_abbreviation}: {exc}. "
                "Fall back to POST /api/news/import for manually-sourced items."
            ) from exc

        items: list[NewsItem] = []
        warnings: list[str] = []
        for article in payload.get("articles", []):
            try:
                items.append(
                    NewsItem(
                        player_name=", ".join(p.get("athlete", {}).get("displayName", "") for p in article.get("categories", []) if p.get("athlete")),
                        headline=article.get("headline", ""),
                        body=article.get("description"),
                        published_at=datetime.datetime.fromisoformat(article["published"].replace("Z", "+00:00")),
                        source="espn",
                        source_url=article.get("links", {}).get("web", {}).get("href"),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"Skipped malformed news row: {exc}")

        return self._result(items, warnings=warnings, raw_meta={"from_cache": from_cache})

    def record_manual_item(
        self, player_name: str, headline: str, body: str | None, source_url: str | None
    ) -> SourceResult[NewsItem]:
        item = NewsItem(
            player_name=player_name,
            headline=headline,
            body=body,
            published_at=datetime.datetime.now(datetime.timezone.utc),
            source="manual",
            source_url=source_url,
        )
        return self._result(item, confidence=SourceConfidence.MEDIUM)
