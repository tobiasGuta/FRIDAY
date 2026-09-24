"""Audio tests use a mock PortAudio backend; no microphone, key, or network required."""

import asyncio

import pytest

from friday.audio.devices import AudioDeviceError, Microphone, Speaker


class Stream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class Backend:
    def __init__(self):
        self.input_stream = None
        self.output_stream = None

    def RawInputStream(self, **kwargs):
        self.input_stream = Stream(**kwargs)
        return self.input_stream

    def RawOutputStream(self, **kwargs):
        self.output_stream = Stream(**kwargs)
        return self.output_stream


def test_microphone_pcm_queue_overflow_and_close():
    async def scenario():
        sd = Backend()
        mic = Microphone(loop=asyncio.get_running_loop(), backend=sd, queue_size=2)
        mic.start()
        assert sd.input_stream.started
        assert sd.input_stream.kwargs["samplerate"] == 16000
        assert sd.input_stream.kwargs["dtype"] == "int16"
        callback = sd.input_stream.kwargs["callback"]
        callback(b"\x01\x00", 1, None, None)
        callback(b"\x02\x00", 1, None, None)
        callback(b"\x03\x00", 1, None, "input overflow")
        await asyncio.sleep(0)
        assert mic.dropped_chunks == 1
        assert mic.status_events == 1
        mic.stop()
        assert sd.input_stream.stopped and sd.input_stream.closed
        assert [chunk async for chunk in mic.chunks()] == [b"\x03\x00"]
        mic.start()
        callback(b"\x04\x00", 1, None, None)
        await asyncio.sleep(0)
        mic.stop()
        assert [chunk async for chunk in mic.chunks()] == [b"\x04\x00"]

    asyncio.run(scenario())


def test_microphone_rejects_duplicate_start_and_invalid_pcm():
    async def scenario():
        sd = Backend()
        mic = Microphone(loop=asyncio.get_running_loop(), backend=sd)
        mic.start()
        with pytest.raises(AudioDeviceError, match="already recording"):
            mic.start()
        mic.stop()

    asyncio.run(scenario())
    with pytest.raises(ValueError):
        Microphone(loop=asyncio.new_event_loop(), sample_rate=8000)


def test_speaker_playback_partial_frames_silence_and_interrupt_flush():
    sd = Backend()
    speaker = Speaker(backend=sd, blocksize=4, max_buffer_seconds=1)
    speaker.start()
    assert sd.output_stream.kwargs["samplerate"] == 24000
    speaker.enqueue(b"\x01\x00\x02\x00\x03\x00", sample_rate=24000)
    callback = sd.output_stream.kwargs["callback"]
    output = bytearray(4)
    callback(output, 2, None, None)
    assert output == b"\x01\x00\x02\x00"
    callback(output, 2, None, None)
    assert output == b"\x03\x00\x00\x00"
    speaker.enqueue(b"\x04\x00", sample_rate=24000)
    speaker.flush()  # Gemini interruption / new speech turn.
    callback(output, 2, None, "output underflow")
    assert output == b"\x00\x00\x00\x00"
    assert speaker.status_events == 1
    speaker.close()
    assert sd.output_stream.stopped and sd.output_stream.closed


def test_speaker_overflow_stays_bounded_and_uses_latest_samples():
    sd = Backend()
    speaker = Speaker(backend=sd, max_buffer_seconds=1)
    pcm = b"\x01\x00" * 24000
    speaker.enqueue(pcm, sample_rate=24000)
    speaker.enqueue(b"\x02\x00" * 10, sample_rate=24000)
    assert speaker.dropped_bytes == 20
    assert len(speaker._pending) == 48000
    assert bytes(speaker._pending[-20:]) == b"\x02\x00" * 10
    with pytest.raises(AudioDeviceError, match="24 kHz"):
        speaker.enqueue(b"\x01\x00", sample_rate=16000)
    with pytest.raises(AudioDeviceError):
        speaker.enqueue(b"\x01", sample_rate=24000)


def test_speaker_drain_waits_for_output_callback():
    async def scenario():
        sd = Backend()
        speaker = Speaker(backend=sd, blocksize=4)
        speaker.start()
        speaker.enqueue(b"\x01\x00\x02\x00", sample_rate=24000)
        waiter = asyncio.create_task(speaker.wait_until_drained(timeout=0.2))
        await asyncio.sleep(0)
        assert not waiter.done()
        sd.output_stream.kwargs["callback"](bytearray(4), 2, None, None)
        assert await waiter is True
        assert speaker.pending_bytes == 0
        speaker.close()

    asyncio.run(scenario())


def test_failed_device_open_is_cleaned_up():
    class BrokenStream(Stream):
        def start(self):
            raise OSError("simulated device failure")

    class BrokenBackend(Backend):
        def RawInputStream(self, **kwargs):
            self.input_stream = BrokenStream(**kwargs)
            return self.input_stream

        def RawOutputStream(self, **kwargs):
            self.output_stream = BrokenStream(**kwargs)
            return self.output_stream

    async def scenario():
        sd = BrokenBackend()
        mic = Microphone(loop=asyncio.get_running_loop(), backend=sd)
        with pytest.raises(AudioDeviceError, match="microphone"):
            mic.start()
        assert not mic.recording and sd.input_stream.closed
        spk = Speaker(backend=sd)
        with pytest.raises(AudioDeviceError, match="speaker"):
            spk.start()
        assert sd.output_stream.closed

    asyncio.run(scenario())


def test_long_speech_waits_for_playback_without_dropping_or_reordering_pcm():
    """A 3-second provider burst must survive a 1-second speaker buffer intact."""

    async def scenario():
        sd = Backend()
        speaker = Speaker(backend=sd, blocksize=12000, max_buffer_seconds=1)
        speaker.start()
        pcm = (b"\x01\x00" * 24000) + (b"\x02\x00" * 24000) + (b"\x03\x00" * 24000)
        enqueue_task = asyncio.create_task(speaker.enqueue_wait(pcm, sample_rate=24000))
        await asyncio.sleep(0)
        assert speaker.pending_bytes == 48000
        assert not enqueue_task.done()
        assert speaker.dropped_bytes == 0
        callback = sd.output_stream.kwargs["callback"]
        output = []
        for _ in range(6):
            block = bytearray(24000)
            callback(block, 12000, None, None)
            output.append(bytes(block))
            await asyncio.sleep(0.02)  # Let the async producer refill the bounded buffer.
            assert 0 <= speaker.pending_bytes <= 48000
        await asyncio.wait_for(enqueue_task, timeout=1)
        assert b"".join(output) == pcm
        assert speaker.played_bytes == len(pcm)
        assert speaker.dropped_bytes == 0
        assert await speaker.wait_until_drained(timeout=0.2)
        speaker.close()

    asyncio.run(scenario())


def test_lossless_speaker_rejects_invalid_pcm_and_stopped_output():
    async def scenario():
        speaker = Speaker(backend=Backend())
        with pytest.raises(AudioDeviceError, match="24 kHz"):
            await speaker.enqueue_wait(b"\x00\x00", sample_rate=16000)
        with pytest.raises(AudioDeviceError, match="24 kHz"):
            await speaker.enqueue_wait(b"\x00", sample_rate=24000)
        with pytest.raises(AudioDeviceError, match="stopped"):
            await speaker.enqueue_wait(b"\x00\x00", sample_rate=24000)
        speaker.start()
        speaker.close()
        with pytest.raises(AudioDeviceError, match="stopped"):
            await speaker.enqueue_wait(b"\x00\x00", sample_rate=24000)

    asyncio.run(scenario())


def test_microphone_reports_inactive_device_but_not_transient_overflow():
    async def scenario():
        sd = Backend()
        mic = Microphone(loop=asyncio.get_running_loop(), backend=sd)
        mic.start()
        stream = sd.input_stream
        stream.active = True
        mic.check_health()
        stream.kwargs["callback"](b"\\x00\\x00", 1, None, "input overflow")
        await asyncio.sleep(0)
        assert mic.status_events == 1
        mic.check_health()  # Overflow is not by itself a disconnect.
        stream.active = False
        with pytest.raises(AudioDeviceError, match="disconnected"):
            mic.check_health()
        mic.stop()
        mic.check_health()  # No probe is needed after clean shutdown.

    asyncio.run(scenario())
