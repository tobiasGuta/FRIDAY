"""Offline managed-scheduler tests; no Google token, network, or real audio."""

import pytest

from friday.schedule import Schedule
from friday.ui import desktop_scheduler
from friday.ui.desktop_scheduler import AlertRequest, SchedulerThread


def test_notification_must_be_attempted_before_reminder_is_acknowledged():
    thread = SchedulerThread(calendar_enabled=False)
    item = Schedule("test", "reminder", "Study", 1000, "pending", 0)

    def acknowledge_without_showing(alert):
        alert.done.set()

    thread.alert.connect(acknowledge_without_showing)
    with pytest.raises(RuntimeError, match="notification unavailable"):
        thread._notify(item)


def test_notification_acknowledges_only_after_tray_attempt():
    thread = SchedulerThread(calendar_enabled=False)
    item = Schedule("test", "timer", "Take a break", 1000, "pending", 0)
    observed = []

    def show(alert):
        observed.append((alert.kind, alert.text))
        alert.attempted = True
        alert.done.set()

    thread.alert.connect(show)
    thread._notify(item)
    assert observed == [("timer", "Take a break")]


def test_desktop_worker_uses_existing_lease_and_disables_console_output(monkeypatch):
    calls = []

    class FakeStore:
        pass

    monkeypatch.setattr(desktop_scheduler, "ScheduleStore", FakeStore)

    def fake_run_worker(store, *, calendar_sync, notify, stop_event, foreground):
        calls.append((store, calendar_sync, notify, stop_event, foreground))

    monkeypatch.setattr(desktop_scheduler, "run_worker", fake_run_worker)
    thread = SchedulerThread(calendar_enabled=False)
    thread.run()
    assert len(calls) == 1
    assert isinstance(calls[0][0], FakeStore)
    assert calls[0][1] is None
    assert calls[0][2] == thread._notify
    assert calls[0][3] is thread._stop
    assert calls[0][4] is False


def test_desktop_worker_does_not_start_after_stop_request(monkeypatch):
    called = []
    monkeypatch.setattr(desktop_scheduler, "ScheduleStore", lambda: object())
    monkeypatch.setattr(
        desktop_scheduler, "run_worker", lambda *_args, **_kwargs: called.append(True)
    )
    thread = SchedulerThread(calendar_enabled=False)
    thread.request_stop()
    thread.run()
    assert called == []


def test_alert_request_starts_unacknowledged():
    request = AlertRequest("reminder", "Study")
    assert not request.attempted
    assert not request.done.is_set()
