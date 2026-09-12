"""Injury-report adapter — ESPN's public site JSON API (no key required).

GET https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{abbrev}/injuries
This is the same undocumented JSON ESPN's own web/app frontend calls.
Some hosting environments/IP ranges get blocked (Akamai bot protection) —
this adapter fails loudly (SourceUnavailableError) rather than pretending
to have fresh injury data, per spec section 30 ("never let stale data
silently appear current"). The recommended fallback is manual import of a
beat-reporter injury report via POST /api/injuries/import.
"""
from __future__ import annotations

import dataclasses

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.data_sources.http_client import ThrottledClient
from app.models.enums import SourceConfidence
from app.settings import get_settings


@dataclasses.dataclass
class InjuryReportRow:
    player_name: str
    team_abbreviation: str
    status: str  # questionable | doubtful | out | ir
    description: str | None
    practice_status: str | None


class InjurySource(DataSource):
    name = "espn"
    default_confidence = SourceConfidence.MEDIUM

    def __init__(self, client: ThrottledClient | None = None):
        self._settings = get_settings()
        self._client = client or ThrottledClient(
            min_interval_seconds=1.0,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )

    def get_team_injuries(self, espn_team_abbreviation: str) -> SourceResult[list[InjuryReportRow]]:
        url = f"{self._settings.espn_api_base_url}/teams/{espn_team_abbreviation.lower()}/injuries"
        try:
            payload, from_cache = self._client.get_json(url, cache_ttl_seconds=600)
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_team_injuries", exc)
            raise SourceUnavailableError(
                f"ESPN injuries endpoint unreachable for {espn_team_abbreviation}: {exc}. "
                "Fall back to POST /api/injuries/import with a manually-sourced report."
            ) from exc

        rows: list[InjuryReportRow] = []
        warnings: list[str] = []
        for item in payload.get("injuries", []):
            try:
                athlete = item.get("athlete", {})
                rows.append(
                    InjuryReportRow(
                        player_name=athlete.get("displayName", ""),
                        team_abbreviation=espn_team_abbreviation.upper(),
                        status=item.get("status", "").lower() or "questionable",
                        description=item.get("shortComment"),
                        practice_status=item.get("details", {}).get("returnDate"),
                    )
                )
            except (KeyError, TypeError) as exc:
                warnings.append(f"Skipped malformed injury row: {exc}")

        return self._result(rows, warnings=warnings, raw_meta={"from_cache": from_cache})
