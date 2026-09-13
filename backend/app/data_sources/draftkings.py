"""DraftKings ingestion adapter.

Two independent, always-available paths (spec section 2 requires we never
depend on a single source, and section 4 requires clear source identity):

1. `DraftKingsApiSource` — calls DraftKings' own public site JSON endpoints,
   the same ones www.draftkings.com's browser client calls to render the
   lobby and lineup builder. No auth/API key. Verified live 2026-09-12:
     - GET https://www.draftkings.com/lobby/getcontests?sport=NFL
     - GET https://api.draftkings.com/draftgroups/v1/draftgroups/{id}/draftables
   These are undocumented and DK can change them without notice — every
   parse step is defensive and raises `SourceUnavailableError` rather than
   returning partial/garbled data, so the ingestion layer can fall back.

2. `DraftKingsCsvSource` — parses DK's own official "Export to CSV" salary
   file (downloaded by the user from the lineup builder — zero scraping,
   zero ToS concerns, and the most robust path since it never breaks when
   DK's internal API shape changes). This is the recommended default.

Both funnel into the same `DraftKingsSlate` / `DraftKingsPlayerRow` shape so
downstream ingestion code never needs to know which path was used.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime
import io
import re

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.data_sources.http_client import ThrottledClient
from app.models.enums import SourceConfidence
from app.settings import get_settings

_DOTNET_DATE_RE = re.compile(r"/Date\((-?\d+)\)/")


def _parse_dotnet_date(value: str) -> datetime.datetime:
    m = _DOTNET_DATE_RE.match(value)
    if not m:
        raise ValueError(f"Unrecognized DK date format: {value!r}")
    millis = int(m.group(1))
    return datetime.datetime.fromtimestamp(millis / 1000, tz=datetime.timezone.utc)


@dataclasses.dataclass
class DraftKingsContestSummary:
    dk_contest_id: str
    name: str
    dk_draft_group_id: str
    start_time_utc: datetime.datetime
    entry_fee: float
    total_prizes: float
    max_entries: int
    is_guaranteed: bool
    game_type: str  # DK's own contest format label, e.g. "Classic", "Showdown Captain Mode",
    # "Single Stat - Total Yards", "In-Game Showdown (Q4)", "Madden Classic", "Snake Showdown",
    # "Best Ball" — only "Classic" is the standard 9-man salary-cap format this app builds for.


@dataclasses.dataclass
class DraftKingsPlayerRow:
    dk_player_id: str
    dk_draftable_id: str | None
    display_name: str
    position: str
    salary: int
    team_abbreviation: str
    opponent_abbreviation: str
    game_description: str
    game_start_time_utc: datetime.datetime | None
    roster_status: str  # active | questionable | out | scratched
    avg_points_per_game: float | None = None


@dataclasses.dataclass
class DraftKingsSlate:
    dk_draft_group_id: str
    players: list[DraftKingsPlayerRow]
    contest_type: str = "classic"


def _roster_status_from_dk_status(raw_status: str) -> str:
    mapping = {
        "None": "active",
        "Q": "questionable",
        "O": "out",
        "D": "doubtful",
        "IR": "out",
        "SUSP": "out",
    }
    return mapping.get(raw_status, "active")


class DraftKingsApiSource(DataSource):
    name = "draftkings_api"
    default_confidence = SourceConfidence.HIGH

    def __init__(self, client: ThrottledClient | None = None):
        settings = get_settings()
        self._settings = settings
        self._client = client or ThrottledClient(min_interval_seconds=settings.dk_request_delay_seconds)

    def get_nfl_contests(self) -> SourceResult[list[DraftKingsContestSummary]]:
        url = f"{self._settings.dk_site_base_url}/lobby/getcontests"
        try:
            payload, from_cache = self._client.get_json(url, params={"sport": "NFL"}, cache_ttl_seconds=120)
        except Exception as exc:  # noqa: BLE001 — any transport/HTTP failure is a source-unavailable condition
            self._log_failure("get_nfl_contests", exc)
            raise SourceUnavailableError(f"DraftKings contests endpoint unreachable: {exc}") from exc

        contests: list[DraftKingsContestSummary] = []
        warnings: list[str] = []
        for raw in payload.get("Contests", []):
            try:
                contests.append(
                    DraftKingsContestSummary(
                        dk_contest_id=str(raw["id"]),
                        name=raw.get("n", ""),
                        dk_draft_group_id=str(raw["dg"]),
                        start_time_utc=_parse_dotnet_date(raw["sd"]),
                        entry_fee=float(raw.get("a", 0.0)),
                        total_prizes=float(raw.get("po", 0.0)),
                        max_entries=int(raw.get("m", 0)),
                        is_guaranteed=str(raw.get("attr", {}).get("IsGuaranteed", "false")).lower() == "true",
                        game_type=raw.get("gameType", ""),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"Skipped malformed contest row: {exc}")

        return self._result(contests, warnings=warnings, raw_meta={"from_cache": from_cache, "count": len(contests)})

    def get_draftables(self, dk_draft_group_id: str) -> SourceResult[DraftKingsSlate]:
        url = f"{self._settings.dk_api_base_url}/draftgroups/v1/draftgroups/{dk_draft_group_id}/draftables"
        try:
            payload, from_cache = self._client.get_json(url, cache_ttl_seconds=90)
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_draftables", exc)
            raise SourceUnavailableError(
                f"DraftKings draftables endpoint unreachable for group {dk_draft_group_id}: {exc}"
            ) from exc

        rows: list[DraftKingsPlayerRow] = []
        warnings: list[str] = []
        for raw in payload.get("draftables", []):
            try:
                comp = raw.get("competition") or {}
                team_abbrev = raw.get("teamAbbreviation", "")
                game_desc = comp.get("name", "")
                opponent = ""
                if " @ " in game_desc:
                    away, home = [s.strip() for s in game_desc.split(" @ ")]
                    opponent = home if team_abbrev == away else away
                start_time = None
                if comp.get("startTime"):
                    start_time = datetime.datetime.fromisoformat(comp["startTime"].replace("Z", "+00:00"))

                rows.append(
                    DraftKingsPlayerRow(
                        dk_player_id=str(raw["playerDkId"]),
                        dk_draftable_id=str(raw.get("draftableId")) if raw.get("draftableId") else None,
                        display_name=raw.get("displayName", ""),
                        position=raw.get("position", ""),
                        salary=int(raw.get("salary", 0)),
                        team_abbreviation=team_abbrev,
                        opponent_abbreviation=opponent,
                        game_description=game_desc,
                        game_start_time_utc=start_time,
                        roster_status=_roster_status_from_dk_status(str(raw.get("status", "None"))),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"Skipped malformed draftable row: {exc}")

        if not rows:
            raise SourceUnavailableError(
                f"DraftKings draftables returned zero usable players for group {dk_draft_group_id}"
            )

        slate = DraftKingsSlate(dk_draft_group_id=str(dk_draft_group_id), players=rows)
        return self._result(slate, warnings=warnings, raw_meta={"from_cache": from_cache, "player_count": len(rows)})


class DraftKingsCsvSource(DataSource):
    """Parses DraftKings' official salary-export CSV — the file produced by
    the "Export to CSV" button in the DK lineup builder. Columns are DK's
    standard classic-contest export format:
    Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame
    """

    name = "draftkings_csv_import"
    default_confidence = SourceConfidence.HIGH

    def parse(self, csv_text: str, dk_draft_group_id: str) -> SourceResult[DraftKingsSlate]:
        reader = csv.DictReader(io.StringIO(csv_text))
        required = {"Position", "Name", "ID", "Salary", "TeamAbbrev"}
        if not required.issubset(set(reader.fieldnames or [])):
            missing = required - set(reader.fieldnames or [])
            raise SourceUnavailableError(f"DK salary CSV missing expected columns: {missing}")

        rows: list[DraftKingsPlayerRow] = []
        warnings: list[str] = []
        for raw in reader:
            try:
                game_info = raw.get("Game Info", "")
                team = raw["TeamAbbrev"]
                opponent = ""
                start_time = None
                m = re.match(r"([A-Z]+)@([A-Z]+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}[AP]M)\s+ET", game_info)
                if m:
                    away, home, date_str, time_str = m.groups()
                    opponent = home if team == away else away
                    naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M%p")
                    start_time = naive.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))

                avg_pts = raw.get("AvgPointsPerGame", "")
                rows.append(
                    DraftKingsPlayerRow(
                        dk_player_id=raw["ID"],
                        dk_draftable_id=None,
                        display_name=raw["Name"],
                        position=raw["Position"],
                        salary=int(float(raw["Salary"])),
                        team_abbreviation=team,
                        opponent_abbreviation=opponent,
                        game_description=game_info,
                        game_start_time_utc=start_time,
                        roster_status="active",
                        avg_points_per_game=float(avg_pts) if avg_pts else None,
                    )
                )
            except (KeyError, ValueError) as exc:
                warnings.append(f"Skipped malformed CSV row: {exc}")

        if not rows:
            raise SourceUnavailableError("DK salary CSV parsed to zero usable player rows")

        slate = DraftKingsSlate(dk_draft_group_id=dk_draft_group_id, players=rows)
        return self._result(slate, warnings=warnings, raw_meta={"player_count": len(rows)})
