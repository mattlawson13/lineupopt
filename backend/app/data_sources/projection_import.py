"""Manual projection import adapter — the supported path for third-party
projection sources (FantasyPros, RotoGrinders, etc.) that require a paid
subscription or forbid scraping in their ToS. We never scrape those; the
user exports/copies their projections to CSV and imports them here.

Expected CSV columns (case-insensitive, flexible ordering):
  name, team, position, projection [, floor, ceiling, ownership]

Also supports market-ownership CSV imports with columns: name, team,
position, ownership.
"""
from __future__ import annotations

import csv
import dataclasses
import io

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.models.enums import SourceConfidence


@dataclasses.dataclass
class ImportedProjectionRow:
    player_name: str
    team: str | None
    position: str | None
    projection: float
    floor: float | None
    ceiling: float | None
    ownership_pct: float | None


class ManualProjectionImportSource(DataSource):
    name = "manual_import"
    default_confidence = SourceConfidence.MEDIUM

    def parse_projection_csv(self, csv_text: str, source_label: str) -> SourceResult[list[ImportedProjectionRow]]:
        reader = csv.DictReader(io.StringIO(csv_text))
        fieldnames = {f.lower().strip(): f for f in (reader.fieldnames or [])}
        if "name" not in fieldnames or "projection" not in fieldnames:
            raise SourceUnavailableError(
                "Imported CSV must have at least 'name' and 'projection' columns "
                f"(found: {list(fieldnames)})"
            )

        rows: list[ImportedProjectionRow] = []
        warnings: list[str] = []
        for raw in reader:
            try:
                def get(col: str) -> str | None:
                    key = fieldnames.get(col)
                    return raw.get(key) if key else None

                rows.append(
                    ImportedProjectionRow(
                        player_name=get("name"),
                        team=get("team"),
                        position=get("position"),
                        projection=float(get("projection")),
                        floor=float(get("floor")) if get("floor") else None,
                        ceiling=float(get("ceiling")) if get("ceiling") else None,
                        ownership_pct=float(get("ownership")) if get("ownership") else None,
                    )
                )
            except (ValueError, TypeError) as exc:
                warnings.append(f"Skipped malformed row: {exc}")

        if not rows:
            raise SourceUnavailableError(f"Imported CSV '{source_label}' produced zero usable rows")

        return self._result(rows, warnings=warnings, raw_meta={"source_label": source_label, "count": len(rows)})
