"""Read-only current and next-day weather from fixed Open-Meteo endpoints.

No IP geolocation, guessed user location, API key, arbitrary URL, or saved history.
The free endpoint is for non-commercial use and requires attribution.
"""

from __future__ import annotations

import json
import math
import re
import threading
from datetime import date
from typing import Any, Literal

from pydantic import Field

from friday.tools.registry import ToolArguments, ToolRegistry, ToolSpec

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_URL = "https://open-meteo.com/en/docs"
WEATHER_TOOL_NAME = "get_weather"


class WeatherArguments(ToolArguments):
    location: str = Field(
        min_length=3, max_length=100,
        description="City and, if known, state or country explicitly supplied by the user.",
    )
    day: Literal["today", "tomorrow"] = Field(
        default="today", description="Forecast day: today or tomorrow."
    )


def _number(value: Any, low: float, high: float) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value) if low <= value <= high else None


def _condition(value: Any) -> str:
    if type(value) is not int:
        return "unknown conditions"
    descriptions = {
        0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "depositing rime fog", 51: "light drizzle",
        53: "moderate drizzle", 55: "dense drizzle", 56: "freezing drizzle",
        57: "dense freezing drizzle", 61: "slight rain", 63: "moderate rain",
        65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
        71: "slight snow", 73: "moderate snow", 75: "heavy snow",
        77: "snow grains", 80: "slight rain showers", 81: "moderate rain showers",
        82: "violent rain showers", 85: "slight snow showers",
        86: "heavy snow showers", 95: "thunderstorm",
        96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
    }
    return descriptions.get(value, "unknown conditions")


def _place_label(place: dict[str, Any]) -> str:
    parts = [place.get(k) for k in ("name", "admin1", "country")]
    return ", ".join(part.strip()[:80] for part in parts if isinstance(part, str) and part.strip())


def _parse_forecast(
    payload: Any, *, place: dict[str, Any], day: Literal["today", "tomorrow"]
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("error"):
        return {"status": "error", "error": "weather_unavailable"}
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        return {"status": "error", "error": "weather_unavailable"}
    index = 0 if day == "today" else 1
    fields = (
        "time", "temperature_2m_max", "temperature_2m_min",
        "precipitation_probability_max", "weather_code",
    )
    if any(not isinstance(daily.get(field), list) or len(daily[field]) <= index
           for field in fields):
        return {"status": "error", "error": "weather_unavailable"}
    try:
        forecast_date = date.fromisoformat(daily["time"][index]).isoformat()
    except (TypeError, ValueError):
        return {"status": "error", "error": "weather_unavailable"}
    high = _number(daily["temperature_2m_max"][index], -200, 160)
    low = _number(daily["temperature_2m_min"][index], -200, 160)
    if high is None or low is None or low > high:
        return {"status": "error", "error": "weather_unavailable"}
    chance = _number(daily["precipitation_probability_max"][index], 0, 100)
    result: dict[str, Any] = {
        "status": "ok",
        "location": _place_label(place),
        "day": day,
        "date": forecast_date,
        "timezone": payload.get("timezone"),
        "conditions": _condition(daily["weather_code"][index]),
        "high_f": high,
        "low_f": low,
        "precipitation_probability_max_percent": chance,
        "source": "Open-Meteo",
        "source_url": SOURCE_URL,
        "attribution": "Weather data by Open-Meteo.com",
        "note": "Forecast, not a guarantee. Null means that value was not provided.",
    }
    if day == "today" and isinstance(payload.get("current"), dict):
        current = payload["current"]
        temperature = _number(current.get("temperature_2m"), -200, 160)
        if temperature is not None:
            result["current_temperature_f"] = temperature
            result["current_conditions"] = _condition(current.get("weather_code"))
            result["apparent_temperature_f"] = _number(
                current.get("apparent_temperature"), -200, 160
            )
            result["wind_speed_mph"] = _number(current.get("wind_speed_10m"), 0, 300)
            if isinstance(current.get("time"), str):
                result["current_observation_time_local"] = current["time"][:25]
    return result


class WeatherService:
    """Make a bounded geocoding request and a bounded forecast request."""

    def __init__(self, *, transport: Any = None) -> None:
        self._transport = transport  # Inject fake transport for offline tests.
        self._lock = threading.Lock()
        self._rate_limited = False

    @staticmethod
    def _read_json(client: Any, url: str, params: dict[str, Any]) -> Any:
        # Fixed URL, no redirects, bounded response, no caller-provided headers.
        with client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 100_000:
                    raise ValueError("oversized weather response")
                chunks.append(chunk)
        return json.loads(b"".join(chunks))

    def lookup(
        self, location: str, day: Literal["today", "tomorrow"] = "today"
    ) -> dict[str, Any]:
        # Optional dependency: loading the voice provider must not require httpx.
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Install weather support: pip install -e '.[web]'") from exc
        # Validate even when called outside the typed tool registry (CLI).
        try:
            args = WeatherArguments.model_validate(
                {"location": location, "day": day}, strict=True
            )
        except ValueError:
            return {"status": "error", "error": "invalid_arguments"}
        if self._rate_limited:
            return {"status": "error", "error": "weather_rate_limited"}
        with self._lock:
            if self._rate_limited:
                return {"status": "error", "error": "weather_rate_limited"}
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(10.0, connect=3.0),
                    follow_redirects=False, transport=self._transport,
                ) as client:
                    places = self._read_json(client, GEOCODE_URL, {
                        "name": args.location.strip(), "count": 10,
                        "language": "en", "format": "json",
                    })
                    if not isinstance(places, dict) or places.get("error"):
                        return {"status": "error", "error": "weather_unavailable"}
                    candidates = places.get("results", [])
                    if not isinstance(candidates, list):
                        return {"status": "error", "error": "weather_unavailable"}
                    unique: dict[str, dict[str, Any]] = {}
                    for place in candidates:
                        if not isinstance(place, dict):
                            continue
                        label = _place_label(place)
                        if label and label not in unique:
                            unique[label] = place
                    if not unique:
                        return {"status": "error", "error": "location_not_found"}
                    if len(unique) != 1:
                        return {
                            "status": "error", "error": "ambiguous_location",
                            "options": list(unique)[:3],
                            "hint": "Ask the user for the city and state or country.",
                        }
                    place = next(iter(unique.values()))
                    lat = _number(place.get("latitude"), -90, 90)
                    lon = _number(place.get("longitude"), -180, 180)
                    timezone = place.get("timezone")
                    if lat is None or lon is None or not isinstance(timezone, str):
                        return {"status": "error", "error": "weather_unavailable"}
                    if re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", timezone) is None:
                        return {"status": "error", "error": "weather_unavailable"}
                    forecast = self._read_json(client, FORECAST_URL, {
                        "latitude": lat, "longitude": lon, "timezone": timezone,
                        "current": (
                            "temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
                        ),
                        "daily": (
                            "weather_code,temperature_2m_max,temperature_2m_min,"
                            "precipitation_probability_max"
                        ),
                        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                        "forecast_days": 2,
                    })
                return _parse_forecast(forecast, place=place, day=args.day)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    self._rate_limited = True
                    return {"status": "error", "error": "weather_rate_limited"}
                return {"status": "error", "error": "weather_unavailable"}
            except (httpx.HTTPError, ValueError, TypeError, OSError):
                return {"status": "error", "error": "weather_unavailable"}


def register_weather(registry: ToolRegistry, service: WeatherService) -> None:
    def handler(args: WeatherArguments) -> dict[str, Any]:
        return service.lookup(args.location, args.day)

    registry.register(ToolSpec(
        name=WEATHER_TOOL_NAME,
        description=(
            "Get current weather and today's or tomorrow's forecast for a city explicitly "
            "named by the user. If location is missing, ask for it; never infer a city from "
            "the clock or user profile. Include state/country when ambiguous. Forecast "
            "temperatures are Fahrenheit and wind speeds mph; rain chance is a percent. "
            "Read-only. Use this instead of web search for weather questions."
        ),
        arguments=WeatherArguments,
        handler=handler,
        notice="Read weather forecast (data by Open-Meteo.com)",
    ))
