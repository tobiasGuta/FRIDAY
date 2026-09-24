import asyncio

from friday.core.events import EventKind, VoiceEvent
from friday.core.session import SessionManager
from friday.providers.fake import FakeVoiceProvider
from friday.ui.cli import (
    VoiceTurnDiagnostics,
    _await_voice_response,
    _voice_events,
    build_parser,
)
from friday.ui.terminal import TerminalCommands


class RecordingSpeaker:
    def __init__(self):
        self.audio = []
        self.flushes = 0

    def enqueue(self, pcm, *, sample_rate):
        self.audio.append((pcm, sample_rate))

    def flush(self):
        self.flushes += 1

    async def wait_until_drained(self, *, timeout=5.0):
        return True


def test_talk_cli_exposes_device_selection_and_timeout():
    args = build_parser().parse_args(
        ["talk", "--input-device", "2", "--output-device", "4", "--max-seconds", "30"]
    )
    assert args.input_device == 2
    assert args.output_device == 4
    assert args.max_seconds == 30


def test_audio_event_consumer_flushes_on_interrupt():
    async def scenario():
        manager = SessionManager(FakeVoiceProvider())
        await manager.start()
        speaker = RecordingSpeaker()
        receiver = asyncio.create_task(_voice_events(manager, speaker))
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x01\x00", sample_rate=24000))
        manager._emit(VoiceEvent(EventKind.INTERRUPTED))
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x02\x00", sample_rate=24000))
        manager._emit(VoiceEvent(EventKind.ERROR, text="mock failure"))
        assert await asyncio.wait_for(receiver, 1) is False
        assert speaker.flushes == 1
        assert speaker.audio == [(b"\x01\x00", 24000), (b"\x02\x00", 24000)]
        await manager.close()

    asyncio.run(scenario())


def test_response_blocks_extra_enters_until_completion():
    async def scenario():
        commands = TerminalCommands()
        speaker = RecordingSpeaker()
        complete = asyncio.Event()
        receiver = asyncio.create_task(asyncio.sleep(5, result=False))
        for _ in range(3):
            commands._offer("")
        async def finish():
            await asyncio.sleep(0.02)
            complete.set()

        complete_task = asyncio.create_task(finish())
        try:
            assert await _await_voice_response(
                commands, receiver, complete, speaker, timeout=0.5
            ) == "ready"
            assert commands._queue.empty()
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, complete_task, return_exceptions=True)

    asyncio.run(scenario())


def test_quit_remains_available_during_response():
    async def scenario():
        commands = TerminalCommands()
        complete = asyncio.Event()
        receiver = asyncio.create_task(asyncio.sleep(5, result=False))
        for _ in range(8):
            commands._offer("")
        commands._offer("/quit")
        try:
            assert await _await_voice_response(
                commands, receiver, complete, RecordingSpeaker(), timeout=0.5
            ) == "quit"
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(scenario())


def test_response_timeout_is_not_extended_by_enter():
    async def scenario():
        commands = TerminalCommands()
        complete = asyncio.Event()
        receiver = asyncio.create_task(asyncio.sleep(5, result=False))
        async def spam():
            for _ in range(30):
                commands._offer("")
                await asyncio.sleep(0.002)
        spam_task = asyncio.create_task(spam())
        try:
            assert await _await_voice_response(
                commands, receiver, complete, RecordingSpeaker(), timeout=0.04
            ) == "timeout"
        finally:
            receiver.cancel()
            spam_task.cancel()
            await asyncio.gather(receiver, spam_task, return_exceptions=True)

    asyncio.run(scenario())


def test_event_consumer_notifies_completion():
    async def scenario():
        manager = SessionManager(FakeVoiceProvider())
        await manager.start()
        finished = asyncio.Event()
        receiver = asyncio.create_task(_voice_events(manager, RecordingSpeaker(), finished))
        manager._emit(VoiceEvent(EventKind.TURN_COMPLETE))
        await asyncio.wait_for(finished.wait(), 1)
        manager._emit(VoiceEvent(EventKind.ERROR, text="mock failure"))
        assert await asyncio.wait_for(receiver, 1) is False
        await manager.close()

    asyncio.run(scenario())


def test_early_completion_survives_until_waiter_and_counts_events():
    async def scenario():
        manager = SessionManager(FakeVoiceProvider())
        await manager.start()
        finished = asyncio.Event()
        diagnostics = VoiceTurnDiagnostics()
        speaker = RecordingSpeaker()
        receiver = asyncio.create_task(_voice_events(manager, speaker, finished, diagnostics))
        commands = TerminalCommands()
        # Simulate a completion arriving before the Enter that stops recording.
        manager._emit(VoiceEvent(EventKind.TRANSCRIPT, text="hello", speaker="user"))
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x00\x00", sample_rate=24000))
        manager._emit(VoiceEvent(EventKind.TURN_COMPLETE))
        await asyncio.wait_for(finished.wait(), timeout=1)
        try:
            assert await _await_voice_response(
                commands, receiver, finished, speaker, timeout=0.1
            ) == "ready"
            assert diagnostics.user_transcript_chunks == 1
            assert diagnostics.assistant_audio_bytes == 2
            assert diagnostics.completions == 1
            assert diagnostics.interruptions == 0
            diagnostics.reset()
            assert diagnostics.completions == 0
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())
