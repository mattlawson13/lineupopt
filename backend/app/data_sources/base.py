"""Data source abstraction layer — see spec section 4.

Every external data provider implements `DataSource` and returns a
`SourceResult`, which always carries a timestamp, a confidence tier, and
any warnings encountered. Nothing in the ingestion/normalization layers
should ever talk to `requests`/`httpx` directly — they go through a
`DataSource` subclass so providers can be swapped without touching
downstream code.
"""
from __future__ import annotations

import abc
import dataclasses
import datetime
import logging
from typing import Any, Generic, TypeVar

from app.models.enums import SourceConfidence

logger = logging.getLogger("lineupopt.data_sources")

T = TypeVar("T")


@dataclasses.dataclass
class SourceResult(Generic[T]):
    data: T
    source_name: str
    fetched_at: datetime.datetime
    confidence: SourceConfidence
    is_stale: bool = False
    warnings: list[str] = dataclasses.field(default_factory=list)
    raw_meta: dict[str, Any] = dataclasses.field(default_factory=dict)


class SourceUnavailableError(RuntimeError):
    """Raised when a source cannot be reached at all. Callers must catch
    this and fall back to cache / manual import rather than crash the
    whole slate build — see ingestion/slate_builder.py.
    """


class DataSource(abc.ABC):
    """Base class for all data providers.

    Subclasses are intentionally heterogeneous in their `fetch_*` methods
    (a weather source and a DraftKings source return very different
    shapes) — what they share is: a stable `name`, a default confidence
    tier, and the requirement to log every failure instead of failing
    silently (spec section 29/30).
    """

    name: str = "unnamed_source"
    default_confidence: SourceConfidence = SourceConfidence.MEDIUM

    def _result(
        self,
        data: T,
        *,
        confidence: SourceConfidence | None = None,
        warnings: list[str] | None = None,
        raw_meta: dict | None = None,
    ) -> SourceResult[T]:
        return SourceResult(
            data=data,
            source_name=self.name,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
            confidence=confidence or self.default_confidence,
            warnings=warnings or [],
            raw_meta=raw_meta or {},
        )

    def _log_failure(self, action: str, exc: Exception) -> None:
        logger.warning("[%s] %s failed: %s", self.name, action, exc)
