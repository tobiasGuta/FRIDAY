"""Offline desktop session tests: no Qt, Gemini, microphone, or credentials."""

import asyncio
from datetime import datetime, timedelta

import pytest

from friday.audio.devices import AudioDeviceError
from friday.core.events import EventKind, VoiceEvent
from friday.core.session import SessionManager
from friday.schedule import ScheduleStore
from friday.ui.desktop_session import DesktopVoiceSession, draft_display
from friday.voice_reminders import DraftReminderArguments, VoiceReminderApproval


class FakeAudioProvider:
    def __init__(self):
        self.events_queue = asyncio.Queue()
        self.closed = False

    async def connect(self):
        return None

    async def send_audio(self, pcm):
        return None

    async def send_text(self, text):
        return None

    async def end_input(self):
        return None

    async def start_activity(self):
        return None

    async def end_activity(self):
        await self.events_queue.put(
            VoiceEvent(EventKind.TRANSCRIPT, text="Yes.", speaker="user")
        )
        await self.events_queue.put(
            VoiceEvent(EventKind.TRANSCRIPT, text="One moment.", speaker="assistant")
        )
        await self.events_queue.put(VoiceEvent(EventKind.TURN_COMPLETE))

    async def events(self):
        while True:
            event = await self.events_queue.get()
            if event is None:
                return
            yield event

    async def close(self):
        self.closed = True
        await self.events_queue.put(None)


class FakeMicrophone:
    dropped_chunks = 0

    def __init__(self):
        self.queue = asyncio.Queue()
        self.recording = False

    def start(self):
        self.recording = True

    def stop(self):
        self.recording = False
        self.queue.put_nowait(None)

    async def chunks(self):
        while True:
            frame = await self.queue.get()
            if frame is None:
                return
            yield frame


class FakeSpeaker:
    def __init__(self):
        self.started = False
        self.closed = False
        self.played_bytes = 0

    def start(self):
        self.started = True

    def flush(self):
        return None

    async def enqueue_wait(self, pcm, *, sample_rate):
        assert sample_rate == 24000
        self.played_bytes += len(pcm)

    async def wait_until_drained(self, *, timeout):
        return True

    def close(self):
        self.closed = True


async def _wait_for(events, kind, value=None):
    async with asyncio.timeout(2):
        while True:
            if any(
                name == kind and (value is None or payload == value)
                for name, payload in events
            ):
                return
            await asyncio.sleep(0.005)


def test_desktop_records_one_turn_and_shuts_down_cleanly():
    async def scenario():
        provider = FakeAudioProvider()
        speaker = FakeSpeaker()
        microphone = FakeMicrophone()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), microphone, speaker,
            approval=None, emit=lambda kind, payload: events.append((kind, payload)),
            max_seconds=30,
        )
        task = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        assert not microphone.recording
        controller.request("start")
        await _wait_for(events, "status", "Listening")
        assert microphone.recording
        controller.request("stop")
        await _wait_for(events, "status", "Responding")
        async with asyncio.timeout(2):
            while len([x for x in events if x == ("status", "Ready")]) < 2:
                await asyncio.sleep(0.005)
        assert not microphone.recording
        assert any(
            kind == "transcript" and value["speaker"] == "assistant"
            for kind, value in events if isinstance(value, dict)
        )
        controller.request("quit")
        await asyncio.wait_for(task, 2)
        assert provider.closed and speaker.closed
        assert events[-1] == ("status", "Disconnected")

    asyncio.run(scenario())


def test_desktop_only_approves_preexisting_draft_in_later_user_turn(tmp_path):
    async def scenario():
        store = ScheduleStore(tmp_path / "private.sqlite3")
        approval = VoiceReminderApproval(store)
        due = (datetime.now().astimezone() + timedelta(days=2)).isoformat()
        assert approval.propose(
            DraftReminderArguments(text="Desktop test", at=due)
        )["status"] == "ok"
        assert store.list_items() == []
        assert draft_display(approval.pending())["action"] == "create"
        events = []
        controller = DesktopVoiceSession(
            SessionManager(FakeAudioProvider()), FakeMicrophone(), FakeSpeaker(),
            approval=approval, emit=lambda kind, value: events.append((kind, value)),
            max_seconds=30,
        )
        task = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        assert any(kind == "draft" and value is not None for kind, value in events)
        assert store.list_items() == []
        controller.request("start")
        await _wait_for(events, "status", "Listening")
        controller.request("stop")
        async with asyncio.timeout(2):
            while not any(
                kind == "result" and value.get("status") == "created"
                for kind, value in events if isinstance(value, dict)
            ):
                await asyncio.sleep(0.005)
        assert len(store.list_items()) == 1
        assert store.list_items()[0].text == "Desktop test"
        assert any(kind == "draft" and value is None for kind, value in events)
        controller.request("quit")
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_draft_display_uses_original_values_for_cancel_and_edit(tmp_path):
    store = ScheduleStore(tmp_path / "draft.sqlite3")
    item = store.reminder(
        (datetime.now().astimezone() + timedelta(days=2)).isoformat(), "Study"
    )
    approval = VoiceReminderApproval(store)
    approval.list_reminders(None)
    from friday.voice_reminders import ReminderIdArguments

    assert approval.propose_cancel(ReminderIdArguments(id=item.id))["status"] == "ok"
    view = draft_display(approval.pending())
    assert view["action"] == "cancel"
    assert view["id"] == item.id
    assert view["before_text"] == "Study"
    assert store.get_pending_reminder(item.id) is not None


def test_provider_failure_stops_voice_without_auto_reconnect():
    async def scenario():
        provider = FakeAudioProvider()
        mic = FakeMicrophone()
        speaker = FakeSpeaker()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), mic, speaker, approval=None,
            emit=lambda kind, value: events.append((kind, value)), max_seconds=30,
        )
        task = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        await provider.events_queue.put(
            VoiceEvent(EventKind.ERROR, text="Provider event stream ended")
        )
        await asyncio.wait_for(task, 2)
        assert provider.closed and speaker.closed and not mic.recording
        assert ("recovery", "connection") in events
        assert events[-1] == ("status", "Disconnected")
        assert events.count(("status", "Connecting")) == 1

    asyncio.run(scenario())


def test_microphone_stream_failure_is_detected_without_stop_click():
    class BrokenMicrophone(FakeMicrophone):
        def check_health(self):
            if self.recording:
                raise AudioDeviceError("Simulated unplug")

    async def scenario():
        provider = FakeAudioProvider()
        mic = BrokenMicrophone()
        speaker = FakeSpeaker()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), mic, speaker, approval=None,
            emit=lambda kind, value: events.append((kind, value)), max_seconds=30,
        )
        task = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        controller.request("start")
        await _wait_for(events, "status", "Listening")
        await asyncio.wait_for(task, 2)
        assert not mic.recording and provider.closed and speaker.closed
        assert ("recovery", "audio") in events
        assert not any("Simulated unplug" in str(value) for _, value in events)
        assert events[-1] == ("status", "Disconnected")

    asyncio.run(scenario())


def test_failed_audio_sender_is_detected_while_still_recording():
    class FailingProvider(FakeAudioProvider):
        async def send_audio(self, pcm):
            raise OSError("secret upstream error")

    async def scenario():
        provider = FailingProvider()
        mic = FakeMicrophone()
        speaker = FakeSpeaker()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), mic, speaker, approval=None,
            emit=lambda kind, value: events.append((kind, value)), max_seconds=30,
        )
        task = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        controller.request("start")
        await _wait_for(events, "status", "Listening")
        await mic.queue.put(bytes([1, 0]))
        await asyncio.wait_for(task, 2)
        assert not mic.recording and provider.closed and speaker.closed
        assert ("recovery", "connection") in events
        assert not any("secret upstream error" in str(value) for _, value in events)

    asyncio.run(scenario())


def test_user_disconnect_does_not_offer_recovery():
    async def scenario():
        provider = FakeAudioProvider()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), FakeMicrophone(), FakeSpeaker(), approval=None,
            emit=lambda kind, value: events.append((kind, value)), max_seconds=30,
        )
        task = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        controller.request("quit")
        await asyncio.wait_for(task, 2)
        assert not any(kind == "recovery" for kind, _ in events)

    asyncio.run(scenario())


def test_session_expiry_warns_and_requires_new_explicit_connection():
    async def scenario():
        provider = FakeAudioProvider()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), FakeMicrophone(), FakeSpeaker(), approval=None,
            emit=lambda kind, value: events.append((kind, value)), max_seconds=5,
        )
        await asyncio.wait_for(controller.run(), timeout=8)
        assert ("recovery", "expired") in events
        assert any(
            kind == "notice" and "nearing its time limit" in str(value)
            for kind, value in events
        )
        assert events.count(("status", "Connecting")) == 1
        assert provider.closed
        assert events[-1] == ("status", "Disconnected")

    asyncio.run(scenario())


def test_failure_discards_pending_draft_without_writing_sqlite(tmp_path):
    async def scenario():
        store = ScheduleStore(tmp_path / "recovery-draft.sqlite3")
        approval = VoiceReminderApproval(store)
        due = (datetime.now().astimezone() + timedelta(days=2)).isoformat()
        assert approval.propose(
            DraftReminderArguments(text="Do not save on reconnect", at=due)
        )["status"] == "ok"
        provider = FakeAudioProvider()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), FakeMicrophone(), FakeSpeaker(),
            approval=approval, emit=lambda k, v: events.append((k, v)), max_seconds=30,
        )
        task = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        await provider.events_queue.put(VoiceEvent(EventKind.ERROR, text="stream lost"))
        await asyncio.wait_for(task, 2)
        assert approval.pending() is None
        assert store.list_items() == []
        assert ("draft", None) in events
        assert ("recovery", "connection") in events

    asyncio.run(scenario())


def test_desktop_session_rejects_unbounded_duration():
    for invalid in (4, 3601):
        with pytest.raises(ValueError, match="5 to 3600"):
            DesktopVoiceSession(
                SessionManager(FakeAudioProvider()),
                FakeMicrophone(), FakeSpeaker(), approval=None,
                emit=lambda *_: None, max_seconds=invalid,
            )


def test_incomplete_generation_is_diagnosed_without_false_turn_completion(monkeypatch):
    from friday.ui import desktop_session

    class NoFinalTurnProvider(FakeAudioProvider):
        async def end_activity(self):
            await self.events_queue.put(
                VoiceEvent(EventKind.TRANSCRIPT, text="I'm here.", speaker="assistant")
            )
            await self.events_queue.put(VoiceEvent(EventKind.GENERATION_COMPLETE))
            # Deliberately no TURN_COMPLETE, as in the suspected live failure.

    async def scenario():
        monkeypatch.setattr(desktop_session, "VOICE_STALL_SECONDS", 0.06)
        provider = NoFinalTurnProvider()
        mic = FakeMicrophone()
        speaker = FakeSpeaker()
        events = []
        controller = DesktopVoiceSession(
            SessionManager(provider), mic, speaker, approval=None,
            emit=lambda kind, value: events.append((kind, value)), max_seconds=30,
        )
        runner = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        controller.request("start")
        await _wait_for(events, "status", "Listening")
        controller.request("stop")
        await _wait_for(events, "status", "Responding")
        await asyncio.wait_for(runner, timeout=2)
        notices = [value for kind, value in events if kind == "notice"]
        assert any(
            "generation_complete=True" in str(value)
            and "turn_complete=False" in str(value)
            and "assistant_text=True" in str(value)
            and "audio_received=False" in str(value)
            and "audio_callback=False" in str(value)
            for value in notices
        )
        assert any("Final turn completion was not received" in str(value)
                   for kind, value in events if kind == "error")
        assert ("recovery", "connection") in events
        assert ("status", "Ready") not in events[events.index(("status", "Responding")) + 1:]
        assert provider.closed and speaker.closed and not mic.recording
        assert controller.completions == 0
        assert controller.generation_completions == 1

    asyncio.run(scenario())


def test_real_turn_complete_releases_microphone_after_generation_marker():
    class CompleteProvider(FakeAudioProvider):
        async def end_activity(self):
            await self.events_queue.put(VoiceEvent(EventKind.GENERATION_COMPLETE))
            await self.events_queue.put(VoiceEvent(EventKind.TURN_COMPLETE))

    async def scenario():
        events = []
        provider = CompleteProvider()
        controller = DesktopVoiceSession(
            SessionManager(provider), FakeMicrophone(), FakeSpeaker(),
            approval=None, emit=lambda kind, value: events.append((kind, value)),
            max_seconds=30,
        )
        runner = asyncio.create_task(controller.run())
        await _wait_for(events, "status", "Ready")
        controller.request("start")
        await _wait_for(events, "status", "Listening")
        controller.request("stop")
        await _wait_for(events, "status", "Responding")
        async with asyncio.timeout(2):
            while events.count(("status", "Ready")) < 2:
                await asyncio.sleep(0.005)
        assert controller.completions == 1
        assert controller.generation_completions == 1
        assert not any("turn-end diagnostics" in str(value) for _, value in events)
        controller.request("quit")
        await asyncio.wait_for(runner, 2)

    asyncio.run(scenario())
