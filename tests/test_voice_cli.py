import asyncio

from friday.core.events import EventKind, VoiceEvent
from friday.core.session import SessionManager
from friday.providers.fake import FakeVoiceProvider
from friday.ui.cli import _voice_events, build_parser


class RecordingSpeaker:
    def __init__(self):
        self.audio = []
        self.flushes = 0

    def enqueue(self, pcm, *, sample_rate):
        self.audio.append((pcm, sample_rate))

    def flush(self):
        self.flushes += 1


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
