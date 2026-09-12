"""Weather adapter — Open-Meteo (free, no API key, verified live).

GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&hourly=...
"""
from __future__ import annotations

import dataclasses
import datetime

from app.data_sources.base import DataSource, SourceResult, SourceUnavailableError
from app.data_sources.http_client import ThrottledClient
from app.models.enums import SourceConfidence
from app.settings import get_settings


@dataclasses.dataclass
class WeatherForecast:
    temperature_f: float | None
    wind_mph: float | None
    wind_direction_deg: float | None
    precipitation_pct: float | None
    precipitation_type: str | None
    humidity_pct: float | None


class WeatherSource(DataSource):
    name = "open_meteo"
    default_confidence = SourceConfidence.MEDIUM

    def __init__(self, client: ThrottledClient | None = None):
        self._settings = get_settings()
        self._client = client or ThrottledClient(min_interval_seconds=0.5)

    def get_forecast_for_kickoff(
        self, latitude: float, longitude: float, kickoff_utc: datetime.datetime, is_dome: bool = False
    ) -> SourceResult[WeatherForecast]:
        if is_dome:
            forecast = WeatherForecast(
                temperature_f=72.0, wind_mph=0.0, wind_direction_deg=None,
                precipitation_pct=0.0, precipitation_type="none", humidity_pct=45.0,
            )
            return self._result(forecast, confidence=SourceConfidence.HIGH, raw_meta={"dome": True})

        url = f"{self._settings.open_meteo_base_url}/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability,relative_humidity_2m",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "forecast_days": 10,
            "timezone": "UTC",
        }
        try:
            payload, from_cache = self._client.get_json(url, params=params, cache_ttl_seconds=1800)
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_forecast_for_kickoff", exc)
            raise SourceUnavailableError(f"Open-Meteo unreachable: {exc}") from exc

        hourly = payload.get("hourly", {})
        times = hourly.get("time", [])
        target = kickoff_utc.strftime("%Y-%m-%dT%H:00")
        if target not in times:
            # kickoff is beyond the forecast window or times don't align exactly;
            # fall back to nearest available hour rather than guessing.
            if not times:
                raise SourceUnavailableError("Open-Meteo returned no hourly data")
            target = min(times, key=lambda t: abs(
                datetime.datetime.fromisoformat(t) - kickoff_utc.replace(tzinfo=None)
            ))
        idx = times.index(target)

        forecast = WeatherForecast(
            temperature_f=_safe_get(hourly, "temperature_2m", idx),
            wind_mph=_safe_get(hourly, "wind_speed_10m", idx),
            wind_direction_deg=_safe_get(hourly, "wind_direction_10m", idx),
            precipitation_pct=_safe_get(hourly, "precipitation_probability", idx),
            precipitation_type="rain" if (_safe_get(hourly, "precipitation_probability", idx) or 0) > 50 else "none",
            humidity_pct=_safe_get(hourly, "relative_humidity_2m", idx),
        )
        is_stale = target != kickoff_utc.strftime("%Y-%m-%dT%H:00")
        return self._result(
            forecast,
            warnings=["Nearest available forecast hour used, not exact kickoff hour"] if is_stale else [],
            raw_meta={"from_cache": from_cache, "matched_hour": target},
        )


def _safe_get(hourly: dict, key: str, idx: int) -> float | None:
    values = hourly.get(key, [])
    return values[idx] if idx < len(values) else None
