"""Local clock tests never contact the API or depend on CI's own timezone."""

from datetime import datetime, timedelta, timezone

import pytest

from friday.tools.local_clock import execute_local_tool, read_local_clock
from friday.ui.cli import main


def fixture_clock():
    return datetime(2026, 9, 23, 21, 53, 12, tzinfo=timezone(timedelta(hours=-4), "EDT"))


def test_local_clock_result_has_one_offset_aware_instant():
    data = read_local_clock(now=fixture_clock)
    assert data == {
        "local_datetime": "2026-09-23T21:53:12-04:00",
        "date": "2026-09-23",
        "time_12h": "9:53:12 PM",
        "time_24h": "21:53:12",
        "weekday": "Wednesday",
        "timezone_name": "EDT",
        "utc_offset": "-04:00",
        "source": "computer_local_clock",
    }


def test_result_tracks_daylight_saving_time_by_supplied_instant():
    clock = lambda: datetime(2026, 12, 1, 8, 2, tzinfo=timezone(timedelta(hours=-5), "EST"))
    data = read_local_clock(now=clock)
    assert data["time_12h"] == "8:02:00 AM"
    assert data["utc_offset"] == "-05:00"
    assert data["timezone_name"] == "EST"


def test_nonlocal_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        read_local_clock(now=lambda: datetime(2026, 9, 23))


def test_strict_allowlist_blocks_unsupported_names_and_arguments():
    calls = []

    def fake_now():
        calls.append("read")
        return fixture_clock()

    assert execute_local_tool("run_shell", {}, now=fake_now) == {
        "status": "error", "error": "unknown_tool"
    }
    assert execute_local_tool("get_local_time", {"command": "whoami"}, now=fake_now) == {
        "status": "error", "error": "invalid_arguments"
    }
    assert execute_local_tool("get_local_time", ["something"], now=fake_now) == {
        "status": "error", "error": "invalid_arguments"
    }
    assert calls == []
    assert execute_local_tool("get_local_time", {}, now=fake_now)["status"] == "ok"
    assert calls == ["read"]


def test_clock_cli_is_offline_and_prints_local_timezone(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("friday.ui.cli.read_local_clock", lambda: read_local_clock(now=fixture_clock))
    assert main(["clock"]) == 0
    assert capsys.readouterr().out.strip() == (
        "Computer local time: 9:53:12 PM on Wednesday, 2026-09-23 (EDT, UTC-04:00)"
    )
