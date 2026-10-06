"""
FLOODGUARD AI — Open-Meteo connector.

Self-contained: no shared connector framework, no shared schema package.
Returns dataclasses, not raw provider JSON, so callers cannot accidentally
depend on Open-Meteo's wire format.

Open-Meteo is a FORECAST source only. It is never used as an observation of
what is actually happening in the channel — that is the ESP32's job.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger()

CURRENT_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "rain",
    "showers",
    "weather_code",
    "surface_pressure",
    "wind_speed_10m",
    "dew_point_2m",
)

HOURLY_FIELDS = (
    "precipitation",
    "precipitation_probability",
    "temperature_2m",
    "relative_humidity_2m",
)

# WMO weather interpretation codes -> (group, description).
_WMO: dict[int, tuple[str, str]] = {
    0: ("Clear", "Clear sky"),
    1: ("Clear", "Mainly clear"),
    2: ("Clouds", "Partly cloudy"),
    3: ("Clouds", "Overcast"),
    45: ("Fog", "Fog"),
    48: ("Fog", "Depositing rime fog"),
    51: ("Rain", "Light drizzle"),
    53: ("Rain", "Moderate drizzle"),
    55: ("Rain", "Dense drizzle"),
    56: ("Rain", "Light freezing drizzle"),
    57: ("Rain", "Dense freezing drizzle"),
    61: ("Rain", "Slight rain"),
    63: ("Rain", "Moderate rain"),
    65: ("Rain", "Heavy rain"),
    66: ("Rain", "Light freezing rain"),
    67: ("Rain", "Heavy freezing rain"),
    71: ("Snow", "Slight snowfall"),
    73: ("Snow", "Moderate snowfall"),
    75: ("Snow", "Heavy snowfall"),
    77: ("Snow", "Snow grains"),
    80: ("Rain", "Slight rain showers"),
    81: ("Rain", "Moderate rain showers"),
    82: ("Rain", "Violent rain showers"),
    85: ("Snow", "Slight snow showers"),
    86: ("Snow", "Heavy snow showers"),
    95: ("Thunderstorm", "Thunderstorm"),
    96: ("Thunderstorm", "Thunderstorm with slight hail"),
    99: ("Thunderstorm", "Thunderstorm with heavy hail"),
}


def describe_weather_code(code: int | None) -> tuple[str, str]:
    if code is None:
        return ("Unknown", "No observation")
    return _WMO.get(int(code), ("Unknown", f"Unmapped WMO code {int(code)}"))


@dataclass(slots=True)
class WeatherObservation:
    timestamp: datetime
    rainfall_mm_hr: float = 0.0
    temperature_c: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    wind_speed_kmh: float | None = None
    weather_code: int | None = None
    weather_main: str = "Unknown"
    weather_description: str = ""
    source: str = "Open-Meteo"


@dataclass(slots=True)
class ForecastSlice:
    time: datetime
    precipitation_mm: float = 0.0
    precipitation_probability_pct: float | None = None
    temperature_c: float | None = None
    humidity_pct: float | None = None


@dataclass(slots=True)
class ForecastResult:
    latitude: float
    longitude: float
    retrieved_at: datetime
    hours: list[ForecastSlice] = field(default_factory=list)
    source: str = "Open-Meteo"


class OpenMeteoError(RuntimeError):
    """Provider call failed. Callers must degrade, never fabricate."""


class OpenMeteoConnector:
    """Thin async client for api.open-meteo.com. No API key required."""

    def __init__(self, timeout: float = 20.0) -> None:
        self._base = get_settings().open_meteo_api_base.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpenMeteoConnector":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        client = self._ensure_client()
        # open_meteo_api_base is the versioned API root; the point-forecast
        # endpoint lives at <base>/forecast.
        url = f"{self._base}/forecast"
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenMeteoError(f"Open-Meteo request failed: {exc}") from exc
        try:
            return resp.json()
        except ValueError as exc:
            raise OpenMeteoError(f"Open-Meteo returned non-JSON body: {exc}") from exc

    @staticmethod
    def _hourly_at(hourly: dict[str, Any], key: str, i: int) -> float | None:
        series = hourly.get(key)
        if not isinstance(series, list) or i >= len(series):
            return None
        value = series[i]
        return None if value is None else float(value)

    async def get_current_weather(self, lat: float, lon: float) -> WeatherObservation:
        data = await self._get(
            {
                "latitude": lat,
                "longitude": lon,
                "current": ",".join(CURRENT_FIELDS),
                "timezone": "UTC",
            }
        )
        current = data.get("current") or {}
        if not current:
            raise OpenMeteoError("Open-Meteo response contained no 'current' block")

        code = current.get("weather_code")
        main, description = describe_weather_code(code)

        ts_raw = current.get("time")
        try:
            timestamp = (
                datetime.fromisoformat(ts_raw).replace(tzinfo=timezone.utc)
                if ts_raw
                else datetime.now(timezone.utc)
            )
        except ValueError:
            timestamp = datetime.now(timezone.utc)

        # Open-Meteo "current" precipitation is an hourly accumulation (mm),
        # so it is already a mm/hr rate over the preceding hour.
        rainfall = current.get("precipitation")
        if rainfall is None:
            rainfall = (current.get("rain") or 0) + (current.get("showers") or 0)

        return WeatherObservation(
            timestamp=timestamp,
            rainfall_mm_hr=float(rainfall or 0.0),
            temperature_c=current.get("temperature_2m"),
            humidity_pct=current.get("relative_humidity_2m"),
            pressure_hpa=current.get("surface_pressure"),
            wind_speed_kmh=current.get("wind_speed_10m"),
            weather_code=None if code is None else int(code),
            weather_main=main,
            weather_description=description,
        )

    async def get_forecast(
        self,
        lat: float,
        lon: float,
        hours: int = 48,
    ) -> ForecastResult:
        data = await self._get(
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(HOURLY_FIELDS),
                "forecast_hours": hours,
                "past_hours": 0,
                "timezone": "UTC",
            }
        )
        block = data.get("hourly") or {}
        times = block.get("time") or []
        if not times:
            raise OpenMeteoError("Open-Meteo response contained no 'hourly.time' series")

        slices: list[ForecastSlice] = []
        for i, raw_time in enumerate(times[:hours]):
            try:
                when = datetime.fromisoformat(raw_time).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            precip = self._hourly_at(block, "precipitation", i)
            slices.append(
                ForecastSlice(
                    time=when,
                    precipitation_mm=float(precip or 0.0),
                    precipitation_probability_pct=self._hourly_at(
                        block, "precipitation_probability", i
                    ),
                    temperature_c=self._hourly_at(block, "temperature_2m", i),
                    humidity_pct=self._hourly_at(block, "relative_humidity_2m", i),
                )
            )

        if not slices:
            raise OpenMeteoError("Open-Meteo returned no parsable forecast hours")

        return ForecastResult(
            latitude=float(data.get("latitude", lat)),
            longitude=float(data.get("longitude", lon)),
            retrieved_at=datetime.now(timezone.utc),
            hours=slices,
        )


async def probe() -> bool:
    """Startup self-check. Returns False instead of raising."""
    try:
        connector = OpenMeteoConnector()
        obs = await connector.get_current_weather(19.0713, 72.8830)
        logger.info("open_meteo_ok", code=obs.weather_code, rain_mm=obs.rainfall_mm_hr)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("open_meteo_unavailable", error=str(exc))
        return False


__all__ = [
    "ForecastResult",
    "ForecastSlice",
    "OpenMeteoConnector",
    "OpenMeteoError",
    "WeatherObservation",
    "asyncio",
    "describe_weather_code",
    "probe",
]