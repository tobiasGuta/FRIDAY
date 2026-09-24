import asyncio

import pytest

from friday.audio import turns as voice_turns
from friday.audio.devices import AudioDeviceError
from friday.audio.turns import VoiceTurns
from friday.core.session import SessionManager
from friday.providers.fake import FakeVoiceProvider


class RecordingProvider(FakeVoiceProvider):
    def __init__(self):
        super().__init__()
        self.actions = []

    async def send_audio(self, pcm):
        self.actions.append(("audio", pcm))

    async def start_activity(self):
        self.actions.append(("start", None))

    async def end_activity(self):
        self.actions.append(("end", None))


class FakeMicrophone:
    def __init__(self):
        self.active = False
        self.queue = asyncio.Queue()
        self.dropped_chunks = 0

    def start(self):
        self.active = True

    def stop(self):
        self.active = False
        self.queue.put_nowait(None)

    async def chunks(self):
        while True:
            frame = await self.queue.get()
            if frame is None:
                return
            yield frame


class FakeSpeaker:
    def __init__(self):
        self.flushes = 0

    def flush(self):
        self.flushes += 1


def test_two_microphone_turns_are_sent_before_end_signal():
    async def scenario():
        provider = RecordingProvider()
        manager = SessionManager(provider)
        await manager.start()
        mic, speaker = FakeMicrophone(), FakeSpeaker()
        turns = VoiceTurns(manager, mic, speaker)
        try:
            await turns.start()
            await mic.queue.put(b"\x01\x00")
            await mic.queue.put(b"\x02\x00")
            await turns.stop()
            assert turns.awaiting_response
            with pytest.raises(RuntimeError, match="still responding"):
                await turns.start()
            assert speaker.flushes == 1
            turns.response_finished()
            await turns.start()
            await mic.queue.put(b"\x03\x00")
            await turns.stop()
            assert provider.actions == [
                ("start", None),
                ("audio", b"\x01\x00"),
                ("audio", b"\x02\x00"),
                ("end", None),
                ("start", None),
                ("audio", b"\x03\x00"),
                ("end", None),
            ]
            assert speaker.flushes == 2
        finally:
            await turns.close()
            await manager.close()

    asyncio.run(scenario())


def test_close_during_recording_does_not_send_end_or_leave_task():
    async def scenario():
        provider = RecordingProvider()
        manager = SessionManager(provider)
        await manager.start()
        mic = FakeMicrophone()
        turns = VoiceTurns(manager, mic, FakeSpeaker())
        await turns.start()
        await turns.close()
        assert not mic.active
        assert turns._sender is None
        assert provider.actions == [("start", None)]
        await manager.close()

    asyncio.run(scenario())


def test_start_failure_closes_microphone_without_sending_audio():
    class RejectStart(RecordingProvider):
        async def start_activity(self):
            raise RuntimeError("start signal rejected")

    async def scenario():
        provider = RejectStart()
        manager = SessionManager(provider)
        await manager.start()
        mic = FakeMicrophone()
        turns = VoiceTurns(manager, mic, FakeSpeaker())
        try:
            with pytest.raises(Exception, match="start_activity"):
                await turns.start()
            assert not mic.active
            assert turns._sender is None
        finally:
            await turns.close()
            await manager.close()

    asyncio.run(scenario())


def test_stalled_audio_sender_is_bounded_and_never_sends_successful_end(monkeypatch):
    class StuckProvider(RecordingProvider):
        async def send_audio(self, pcm):
            await asyncio.Event().wait()

    async def scenario():
        monkeypatch.setattr(voice_turns, "SEND_DRAIN_TIMEOUT_SECONDS", 0.03)
        provider = StuckProvider()
        manager = SessionManager(provider)
        await manager.start()
        mic = FakeMicrophone()
        turns = VoiceTurns(manager, mic, FakeSpeaker())
        try:
            await turns.start()
            await mic.queue.put(bytes([1, 0]))
            # The sender should have entered the provider before Stop drains.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            with pytest.raises(AudioDeviceError, match="stalled"):
                await turns.stop()
            assert ("end", None) not in provider.actions
            assert not mic.active
            assert not turns.recording
        finally:
            await turns.close()
            await manager.close()

    asyncio.run(scenario())
