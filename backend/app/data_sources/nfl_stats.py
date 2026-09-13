"""NFL historical/statistical data adapter — nflverse-data (FREE, public
GitHub release assets, no API key). Verified live 2026-09-12/13:

  - schedules/games.csv
  - player_stats/player_stats_{season}.csv  (per-season; ~5.5MB for one
    season, vs. ~125MB in memory for the combined 1999-present file —
    that combined file is also served at player_stats/player_stats.csv,
    but we deliberately never touch it: parsing it peaks well over
    Render's free-tier 512MB limit even before any filtering, which is
    what actually OOM-killed a live deployment)
  - rosters/roster_{season}.csv
  - weekly_rosters/roster_weekly_{season}.csv
  - depth_charts/depth_charts_{season}.csv  (full daily archive since
    preseason — 500K+ rows / ~260MB parsed whole; get_depth_chart() below
    stream-parses and keeps only the latest date's ~2-3K rows for exactly
    the same reason as player_stats above — this one also OOM-killed a
    live deployment, confirmed 2026-09-13)
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
import os
import tempfile

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

    def get_player_stats(self, season: int) -> SourceResult[pd.DataFrame]:
        """One season's stats only (~5.5MB). Raises SourceUnavailableError
        if nflverse hasn't published this season's file yet (e.g. it's the
        current season and week 1 hasn't happened) — callers that want a
        "most recent available" fallback should retry with earlier
        seasons rather than requesting multiple seasons in one call here.
        """
        df, from_cache = self._get_csv("player_stats", f"player_stats_{season}.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_roster(self, season: int) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("rosters", f"roster_{season}.csv", cache_ttl_seconds=86400)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_weekly_roster(self, season: int) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("weekly_rosters", f"roster_weekly_{season}.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_depth_chart(self, season: int) -> SourceResult[pd.DataFrame]:
        """Only the most recent depth-chart snapshot — depth_charts_{season}.csv
        is a full daily archive since preseason (500K+ rows, ~260MB once
        parsed with pandas' default full-file read), and reading it whole is
        what actually OOM-killed a live 512MB Render deployment (confirmed
        2026-09-13). The file is emitted newest-date-first with each day's
        snapshot as a contiguous block (~2-3K rows), so we stream-parse in
        chunks and stop as soon as we've collected every row for the first
        (most recent) date seen, instead of materializing months of history
        just to keep today's snapshot.

        Chunked reading has to go through a real temp file, not
        `io.StringIO(text)`: verified pandas' C parser only honors
        `chunksize` as true incremental streaming from an actual file —
        from a StringIO it silently reads the whole buffer upfront
        regardless of chunksize (~200MB peak for one "chunk" vs ~3MB peak
        reading the same data from a file), which would have defeated the
        entire point of this fix.
        """
        url = f"{self._settings.nflverse_data_base_url}/depth_charts/depth_charts_{season}.csv"
        try:
            text, from_cache = self._client.get_text(url, cache_ttl_seconds=3600)
        except Exception as exc:  # noqa: BLE001
            self._log_failure(f"get_depth_chart({season})", exc)
            raise SourceUnavailableError(f"nflverse-data asset unreachable: depth_charts/depth_charts_{season}.csv: {exc}") from exc

        fd, tmp_path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            del text

            latest_dt = None
            matched_chunks: list[pd.DataFrame] = []
            for chunk in pd.read_csv(tmp_path, chunksize=5000):
                if latest_dt is None:
                    latest_dt = chunk["dt"].iloc[0]
                matching = chunk[chunk["dt"] == latest_dt]
                matched_chunks.append(matching)
                if len(matching) < len(chunk):
                    # This chunk crossed into an older date. Every later
                    # chunk is strictly older (newest-first ordering), so
                    # everything for latest_dt has now been collected.
                    break
        finally:
            os.remove(tmp_path)

        df = pd.concat(matched_chunks, ignore_index=True) if matched_chunks else pd.DataFrame()
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})

    def get_snap_counts(self, season: int) -> SourceResult[pd.DataFrame]:
        df, from_cache = self._get_csv("snap_counts", f"snap_counts_{season}.csv", cache_ttl_seconds=3600)
        return self._result(df, raw_meta={"from_cache": from_cache, "rows": len(df)})
