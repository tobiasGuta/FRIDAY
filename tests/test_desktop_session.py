"""Offline desktop session tests: no Qt, Gemini, microphone, or credentials."""

import asyncio
from datetime import datetime, timedelta

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
