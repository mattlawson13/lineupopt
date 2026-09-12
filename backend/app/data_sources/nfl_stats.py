"""NFL historical/statistical data adapter — nflverse-data (FREE, public
GitHub release assets, no API key). Verified live 2026-09-12:

  - schedules/games.csv
  - player_stats/player_stats.csv
  - rosters/roster_{season}.csv
  - weekly_rosters/roster_weekly_{season}.csv
  - depth_charts/depth_charts_{season}.csv
  - snap_counts/snap_counts_{season}.csv

nflverse-data is the community-maintained successor to nflfastR's data
releases and is the standard free source for NFL play-by-play-derived
stats, rosters, snap counts, and depth charts. We deliberately do NOT wire
up an NGS (Next Gen Stats) asset here — its exact release/filename wasn't
confirmed reachable, and per project policy we don't fabricate endpoints;
add it once verified.

Returns pandas DataFrames — these feed features/usage_features.py directly.
"""
from __future__ import annotations

import io

import pandas as pd

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.data_sources.http_client import ThrottledClient
from app.models.enums import SourceConfidence
from app.settings import get_settings


class NFLStatsSource(DataSource):
    name = "nflverse"
    default_confidence = SourceConfidence.HIGH

    def __init__(self, client: ThrottledClient | None = None):
        self._settings = get_settings()
        self._client = client or ThrottledClient(min_interval_seconds=0.5)

    def _get_csv(self, release_tag: str, filename: str, cache_ttl_seconds: int) -> tuple[pd.DataFrame, bool]:
        url = f"{self._settings.nflverse_data_base_url}/{release_tag}/{filename}"
        try:
            text, from_cache = self._client.get_text(url, cache_ttl_seconds=cache_ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            self._log_failure(f"_get_csv({release_tag}/{filename})", exc)
            raise SourceUnavailableError(f"nflverse-data asset unreachable: {release_tag}/{filename}: {exc}") from exc
        return pd.read_csv(io.StringIO(text), low_memory=False), from_cache

    def get_schedules(self) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("schedules", "games.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_player_stats(self) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("player_stats", "player_stats.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_roster(self, season: int) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("rosters", f"roster_{season}.csv", cache_ttl_seconds=86400)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_weekly_roster(self, season: int) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("weekly_rosters", f"roster_weekly_{season}.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_depth_chart(self, season: int) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("depth_charts", f"depth_charts_{season}.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_snap_counts(self, season: int) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("snap_counts", f"snap_counts_{season}.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})
