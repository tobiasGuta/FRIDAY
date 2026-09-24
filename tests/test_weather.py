"""Offline Open-Meteo tests; no network, microphone, or API keys."""

import asyncio
from types import SimpleNamespace as Obj

import httpx
import pytest

from friday.config import Settings
from friday.core.events import EventKind
from friday.providers.gemini_live import GeminiLiveProvider
from friday.tools.builtins import build_builtin_registry
from friday.tools.weather import (
    FORECAST_URL, GEOCODE_URL, WeatherArguments, WeatherService, _condition,
)
from friday.ui.cli import build_parser, main
from test_clock_live import install_mock_sdk


PLACE = {
    "name": "Brooklyn", "admin1": "New York", "country": "United States",
    "latitude": 40.65, "longitude": -73.95, "timezone": "America/New_York",
}
FORECAST = {
    "timezone": "America/New_York",
    "current": {
        "time": "2026-09-24T09:00", "temperature_2m": 68.2,
        "apparent_temperature": 67.3, "wind_speed_10m": 9.5, "weather_code": 2,
    },
    "daily": {
        "time": ["2026-09-24", "2026-09-25"],
        "temperature_2m_max": [76, 74], "temperature_2m_min": [60, 58],
        "precipitation_probability_max": [30, 80], "weather_code": [2, 61],
    },
}


def _service(places=None, forecast=None, status=None):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https"
        if status:
            return httpx.Response(status)
        if str(request.url).startswith(GEOCODE_URL):
            assert request.url.params["count"] == "10"
            return httpx.Response(200, json={"results": [PLACE] if places is None else places})
        assert str(request.url).startswith(FORECAST_URL)
        assert request.url.params["forecast_days"] == "2"
        assert request.url.params["temperature_unit"] == "fahrenheit"
        assert request.url.params["wind_speed_unit"] == "mph"
        return httpx.Response(200, json=FORECAST if forecast is None else forecast)

    return WeatherService(transport=httpx.MockTransport(handle)), calls


def test_today_and_tomorrow_use_one_geocode_and_one_forecast_each():
    service, calls = _service()
    today = service.lookup("Brooklyn, New York")
    assert today["status"] == "ok"
    assert today["location"] == "Brooklyn, New York, United States"
    assert today["date"] == "2026-09-24"
    assert today["high_f"] == 76
    assert today["low_f"] == 60
    assert today["current_temperature_f"] == 68.2
    assert today["wind_speed_mph"] == 9.5
    assert today["precipitation_probability_max_percent"] == 30
    assert today["source"] == "Open-Meteo"
    tomorrow = service.lookup("Brooklyn, New York", "tomorrow")
    assert tomorrow["date"] == "2026-09-25"
    assert tomorrow["conditions"] == "slight rain"
    assert tomorrow["precipitation_probability_max_percent"] == 80
    assert "current_temperature_f" not in tomorrow
    assert len(calls) == 4
    assert calls[0].url.params["name"] == "Brooklyn, New York"


def test_ambiguous_city_never_calls_forecast():
    other = dict(PLACE, admin1="Connecticut", latitude=41.0)
    service, calls = _service(places=[PLACE, other])
    result = service.lookup("Brooklyn")
    assert result["error"] == "ambiguous_location"
    assert result["options"] == [
        "Brooklyn, New York, United States",
        "Brooklyn, Connecticut, United States",
    ]
    assert len(calls) == 1


def test_unknown_location_never_calls_forecast():
    service, calls = _service(places=[])
    assert service.lookup("Nowhere") == {"status": "error", "error": "location_not_found"}
    assert len(calls) == 1


def test_bad_forecast_or_missing_required_temperature_is_rejected():
    broken = {**FORECAST, "daily": {**FORECAST["daily"], "temperature_2m_max": [None, 74]}}
    service, _ = _service(forecast=broken)
    assert service.lookup("Brooklyn, New York")["error"] == "weather_unavailable"


def test_missing_precipitation_does_not_invent_chance():
    forecast = {**FORECAST, "daily": {
        **FORECAST["daily"], "precipitation_probability_max": [None, None],
    }}
    service, _ = _service(forecast=forecast)
    result = service.lookup("Brooklyn, New York")
    assert result["status"] == "ok"
    assert result["precipitation_probability_max_percent"] is None


def test_rate_limit_circuits_after_first_request():
    service, calls = _service(status=429)
    expected = {"status": "error", "error": "weather_rate_limited"}
    assert service.lookup("Brooklyn, New York") == expected
    assert service.lookup("Paris, France") == expected
    assert len(calls) == 1


def test_strict_location_and_day():
    for value in [{"location": ""}, {"location": "Paris", "day": "next_week"},
                  {"location": "Paris", "extra": "unknown"}, {"location": 42}]:
        with pytest.raises(ValueError):
            WeatherArguments.model_validate(value, strict=True)


def test_unknown_codes_and_nonfinite_value_fail_safe():
    assert _condition(999) == "unknown conditions"
    assert _condition(True) == "unknown conditions"
    forecast = {**FORECAST, "daily": {**FORECAST["daily"],
        "temperature_2m_min": [float("nan"), 58]}}
    service, _ = _service(forecast=forecast)
    assert service.lookup("Brooklyn, New York")["error"] == "weather_unavailable"


def test_redirect_is_not_followed():
    def redirect(_request):
        return httpx.Response(302, headers={"Location": "https://evil.example/weather"})
    service = WeatherService(transport=httpx.MockTransport(redirect))
    assert service.lookup("Brooklyn, New York")["error"] == "weather_unavailable"


def test_weather_registry_default_disabled_and_strict_declaration(monkeypatch):
    assert build_builtin_registry().available_tools() == ()
    registry = build_builtin_registry(enable_local_clock=True, enable_weather=True)
    decls = registry.declarations()
    assert [decl["name"] for decl in decls] == ["get_local_time", "get_weather"]
    assert decls[1]["parameters"]["properties"]["location"]["type"] == "STRING"
    assert decls[1]["parameters"]["properties"]["day"]["type"] == "STRING"
    assert decls[1]["parameters"]["required"] == ["location"]
    monkeypatch.setattr(WeatherService, "lookup", lambda _self, loc, day: {
        "status": "ok", "location": loc, "day": day,
    })
    assert registry.execute("get_weather", {"location": "Paris, France"}) == {
        "status": "ok", "location": "Paris, France", "day": "today",
    }
    assert registry.execute("get_weather", {"location": "Paris", "day": "next week"}) == {
        "status": "error", "error": "invalid_arguments",
    }
    assert registry.execute("get_weather", {"location": "Paris", "shell": "whoami"}) == {
        "status": "error", "error": "invalid_arguments",
    }


def test_voice_weather_tool_roundtrip_and_no_search(monkeypatch):
    call = Obj(id="weather-1", name="get_weather",
               args={"location": "Brooklyn, New York", "day": "tomorrow"})
    session, clients = install_mock_sdk(monkeypatch, [call])
    observed = []
    monkeypatch.setattr(WeatherService, "lookup", lambda _self, loc, day: (
        observed.append((loc, day)) or {
            "status": "ok", "day": day, "location": loc,
            "high_f": 74, "low_f": 58, "source": "Open-Meteo",
        }
    ))

    async def scenario():
        adapter = GeminiLiveProvider(
            Settings(_env_file=None, GEMINI_API_KEY="mock-key"),
            enable_local_clock=True, enable_weather=True,
        )
        await adapter.connect()
        try:
            decls = clients[0].config.tools[0]["function_declarations"]
            assert [item["name"] for item in decls] == ["get_local_time", "get_weather"]
            events = []
            async with asyncio.timeout(2):
                async for event in adapter.events():
                    events.append(event)
                    if event.kind is EventKind.TURN_COMPLETE:
                        break
            result = session.tool_responses[0].response["result"]
            assert result["high_f"] == 74
            assert result["source"] == "Open-Meteo"
            assert events[0].kind is EventKind.NOTICE
            assert events[-1].kind is EventKind.TURN_COMPLETE
            assert observed == [("Brooklyn, New York", "tomorrow")]
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_weather_cli_dispatch_no_microphone(monkeypatch, capsys):
    monkeypatch.setattr(WeatherService, "lookup", lambda _self, loc, day: {
        "status": "ok", "location": loc, "date": "2026-09-24", "conditions": "partly cloudy",
        "high_f": 76.0, "low_f": 60.0, "precipitation_probability_max_percent": 30.0,
        "source_url": "https://open-meteo.com/en/docs",
    })
    assert main(["weather", "--location", "Brooklyn, New York"]) == 0
    output = capsys.readouterr().out
    assert "Weather data by Open-Meteo.com" in output
    assert "30%" in output
    assert "Brooklyn, New York" in output
    assert build_parser().parse_args(["weather", "--location", "Paris"]).day == "today"
