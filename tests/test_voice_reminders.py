"""Offline approval-gate regressions: no Google, Gemini quota, or microphone."""

import asyncio
from datetime import datetime, timedelta

import pytest

from friday.config import Settings
from friday.core.events import EventKind, VoiceEvent
from friday.core.session import SessionManager
from friday.providers.fake import FakeVoiceProvider
from friday.providers.gemini_live import GeminiLiveProvider
from friday.schedule import ScheduleStore
from friday.tools.registry import ToolRegistry
from friday.ui.cli import _voice_events, build_parser
from friday.voice_reminders import (
    DRAFT_TOOL_NAME,
    DraftReminderArguments,
    VoiceReminderApproval,
    register_reminder_draft,
)


def future_at() -> str:
    return (datetime.now().astimezone() + timedelta(days=2)).isoformat(timespec="seconds")


def ready(tmp_path):
    store = ScheduleStore(tmp_path / "approval.sqlite3")
    approval = VoiceReminderApproval(store)
    registry = ToolRegistry()
    register_reminder_draft(registry, approval)
    return store, approval, registry


def test_voice_draft_is_opt_in_and_only_a_proposal(tmp_path):
    store, approval, registry = ready(tmp_path)
    assert not build_parser().parse_args(["talk"]).reminders
    assert build_parser().parse_args(["talk", "--reminders"]).reminders
    assert [item["name"] for item in registry.declarations()] == [DRAFT_TOOL_NAME]
    at = future_at()
    result = registry.execute(DRAFT_TOOL_NAME, {"text": "Study cybersecurity", "at": at})
    assert result["status"] == "ok"
    assert result["state"] == "pending_approval"
    assert result["at"] == at
    assert approval.pending() is not None
    assert store.list_items(include_history=True) == []
    assert registry.execute("run_shell", {"cmd": "whoami"})["error"] == "unknown_tool"
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": "Study cybersecurity", "at": at, "approved": True
    })["error"] == "invalid_arguments"
    assert store.list_items(include_history=True) == []


def test_proposal_and_yes_in_same_turn_cannot_create(tmp_path):
    store, approval, registry = ready(tmp_path)
    approval.begin_turn()
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": "Review notes", "at": future_at()
    })["status"] == "ok"
    approval.hear_user("Yes, create that reminder.")
    assert approval.finish_turn() is None
    assert approval.pending() is not None
    assert store.list_items(include_history=True) == []


@pytest.mark.parametrize("phrase", [
    "Yes.", "Yes, create that reminder!", "Approve reminder.", "Confirm the reminder."
])
def test_explicit_spoken_approval_in_next_turn_creates_exactly_once(tmp_path, phrase):
    store, approval, registry = ready(tmp_path)
    registry.execute(DRAFT_TOOL_NAME, {"text": "Review notes", "at": future_at()})
    approval.begin_turn()
    approval.hear_user(phrase)
    created = approval.finish_turn()
    assert created is not None and created["status"] == "created"
    assert len(store.list_items(include_history=True)) == 1
    assert store.list_items(include_history=True)[0].id == created["id"]
    assert approval.finish_turn() is None
    assert approval.approve()["error"] == "no_pending_reminder"
    assert len(store.list_items(include_history=True)) == 1


@pytest.mark.parametrize("phrase", [
    "yes, and delete everything", "yes I guess", "okay", "the assistant said yes",
    "we should create a reminder tomorrow",
])
def test_non_exact_or_ambiguous_speech_does_not_approve(tmp_path, phrase):
    store, approval, registry = ready(tmp_path)
    registry.execute(DRAFT_TOOL_NAME, {"text": "Review notes", "at": future_at()})
    approval.begin_turn()
    approval.hear_user(phrase)
    assert approval.finish_turn() is None
    assert approval.pending() is not None
    assert store.list_items(include_history=True) == []


def test_decline_and_terminal_fallback(tmp_path):
    store, approval, registry = ready(tmp_path)
    at = future_at()
    registry.execute(DRAFT_TOOL_NAME, {"text": "Review notes", "at": at})
    approval.begin_turn()
    approval.hear_user("Cancel reminder.")
    assert approval.finish_turn() == {"status": "rejected"}
    assert approval.approve()["error"] == "no_pending_reminder"
    assert store.list_items(include_history=True) == []
    registry.execute(DRAFT_TOOL_NAME, {"text": "Review notes", "at": at})
    assert approval.approve()["status"] == "created"
    assert len(store.list_items(include_history=True)) == 1


def test_invalid_times_text_and_pending_replacement_are_denied(tmp_path):
    store, approval, registry = ready(tmp_path)
    for at in ("tomorrow at 7", "2026-09-25T19:00:00", "not a date"):
        assert registry.execute(DRAFT_TOOL_NAME, {
            "text": "Study", "at": at
        })["status"] == "error"
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": " \t ", "at": future_at()
    })["error"] == "invalid_text"
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": "hello\nworld", "at": future_at()
    })["error"] == "invalid_text"
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": "Study", "at": future_at()
    })["status"] == "ok"
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": "Overwrite draft", "at": future_at()
    })["error"] == "pending_approval"
    assert approval.pending().text == "Study"
    assert store.list_items(include_history=True) == []


def test_draft_expires_and_shutdown_never_persists(tmp_path, monkeypatch):
    store, approval, registry = ready(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("friday.voice_reminders.monotonic", lambda: clock[0])
    registry.execute(DRAFT_TOOL_NAME, {"text": "Study", "at": future_at()})
    approval.begin_turn()
    clock[0] += 301
    approval.hear_user("Yes.")
    assert approval.finish_turn() is None
    assert approval.approve()["error"] == "no_pending_reminder"
    assert store.list_items(include_history=True) == []


def test_transcript_overflow_and_interrupt_fail_closed(tmp_path):
    store, approval, registry = ready(tmp_path)
    registry.execute(DRAFT_TOOL_NAME, {"text": "Study", "at": future_at()})
    approval.begin_turn()
    approval.hear_user("x" * 301)
    approval.hear_user("yes")
    assert approval.finish_turn() is None
    approval.begin_turn()
    approval.hear_user("yes")
    approval.abort_turn()
    assert approval.finish_turn() is None
    assert store.list_items(include_history=True) == []


def test_only_user_transcript_can_approve_via_event_consumer(tmp_path, capsys):
    class SilentSpeaker:
        def flush(self):
            pass

        async def enqueue_wait(self, *_args, **_kwargs):
            return None

    async def scenario():
        store, approval, registry = ready(tmp_path)
        manager = SessionManager(FakeVoiceProvider())
        await manager.start()
        task = asyncio.create_task(_voice_events(
            manager, SilentSpeaker(), reminder_approval=approval
        ))
        registry.execute(DRAFT_TOOL_NAME, {"text": "Review notes", "at": future_at()})
        approval.begin_turn()
        manager._emit(VoiceEvent(EventKind.TRANSCRIPT, text="yes", speaker="assistant"))
        manager._emit(VoiceEvent(EventKind.TURN_COMPLETE))
        manager._emit(VoiceEvent(EventKind.ERROR, text="test stop"))
        assert await asyncio.wait_for(task, 1) is False
        assert store.list_items(include_history=True) == []
        await manager.close()

        manager2 = SessionManager(FakeVoiceProvider())
        await manager2.start()
        task2 = asyncio.create_task(_voice_events(
            manager2, SilentSpeaker(), reminder_approval=approval
        ))
        approval.begin_turn()
        manager2._emit(VoiceEvent(EventKind.TRANSCRIPT, text="Yes, create", speaker="user"))
        manager2._emit(VoiceEvent(EventKind.TRANSCRIPT, text="that reminder.", speaker="user"))
        manager2._emit(VoiceEvent(EventKind.TURN_COMPLETE))
        manager2._emit(VoiceEvent(EventKind.ERROR, text="test stop"))
        assert await asyncio.wait_for(task2, 1) is False
        assert len(store.list_items(include_history=True)) == 1
        await manager2.close()

    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert output.count("REMINDER CREATED") == 1


def test_mock_gemini_declares_draft_only_when_explicitly_enabled(monkeypatch, tmp_path):
    from test_clock_live import install_mock_sdk

    _session, clients = install_mock_sdk(monkeypatch, [])

    async def scenario():
        settings = Settings(_env_file=None, GEMINI_API_KEY="mock-key")
        ordinary = GeminiLiveProvider(settings, enable_local_clock=True)
        await ordinary.connect()
        assert [d["name"] for d in clients[0].config.tools[0]["function_declarations"]] == [
            "get_local_time"
        ]
        await ordinary.close()
        store, approval, _registry = ready(tmp_path)
        enabled = GeminiLiveProvider(
            settings, enable_local_clock=True, reminder_approval=approval
        )
        await enabled.connect()
        try:
            assert [d["name"] for d in clients[1].config.tools[0]["function_declarations"]] == [
                "get_local_time", DRAFT_TOOL_NAME
            ]
            assert "application controls approval" in clients[1].config.system_instruction
            assert store.list_items(include_history=True) == []
        finally:
            await enabled.close()

    asyncio.run(scenario())


def test_argument_validation_is_strict_and_independent_of_model(tmp_path):
    store, approval, registry = ready(tmp_path)
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": 42, "at": future_at()
    })["error"] == "invalid_arguments"
    assert registry.execute(DRAFT_TOOL_NAME, {
        "text": "Study", "at": 123456789
    })["error"] == "invalid_arguments"
    args = DraftReminderArguments(text="Study", at=future_at())
    assert approval.propose(args)["status"] == "ok"
    assert store.list_items(include_history=True) == []
