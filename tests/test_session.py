import asyncio

import pytest

from friday.core.events import EventKind, SessionState, VoiceEvent
from friday.core.provider import ProviderCapabilityError
from friday.core.session import SessionError, SessionManager
from friday.providers.fake import FakeVoiceProvider


async def _next_turn(manager: SessionManager):
    output = []
    async for event in manager.events():
        output.append(event)
        if event.kind is EventKind.TURN_COMPLETE:
            break
    return output


def test_fake_provider_conversation_and_lifecycle():
    async def scenario():
        provider = FakeVoiceProvider()
        manager = SessionManager(provider)
        await manager.start()
        assert manager.state is SessionState.READY
        await manager.send_text("Hi FRIDAY")
        messages = await asyncio.wait_for(_next_turn(manager), timeout=1)
        assert [e.text for e in messages if e.kind is EventKind.TRANSCRIPT] == [
            "Hi FRIDAY", "Echo: Hi FRIDAY"
        ]
        stats = manager.metrics()
        assert stats.provider_events == 3
        assert stats.output_audio_bytes == 0
        assert stats.elapsed_seconds >= 0
        await manager.close()
        await manager.close()
        assert manager.state is SessionState.CLOSED
        assert not provider.connected
    asyncio.run(scenario())


def test_start_twice_and_send_before_start():
    async def scenario():
        manager = SessionManager(FakeVoiceProvider())
        with pytest.raises(SessionError):
            await manager.send_text("premature")
        await manager.start()
        with pytest.raises(SessionError):
            await manager.start()
        with pytest.raises(ValueError):
            await manager.send_text("  ")
        with pytest.raises(ProviderCapabilityError):
            await manager.send_audio(b"\x00\x00")
        assert manager.state is SessionState.READY
        await manager.close()
        with pytest.raises(SessionError):
            await manager.send_text("too late")
    asyncio.run(scenario())


def test_connection_failure_and_close():
    async def scenario():
        manager = SessionManager(FakeVoiceProvider(fail_connect=True))
        with pytest.raises(SessionError, match="connection failed"):
            await manager.start()
        assert manager.state is SessionState.FAILED
        await manager.close()
        assert manager.state is SessionState.CLOSED
    asyncio.run(scenario())


def test_send_failure_emits_error():
    async def scenario():
        manager = SessionManager(FakeVoiceProvider(fail_send=True))
        await manager.start()
        with pytest.raises(SessionError, match="send_text"):
            await manager.send_text("trigger")
        assert manager.state is SessionState.FAILED
        events = []
        async for event in manager.events():
            events.append(event)
            if event.kind is EventKind.ERROR:
                break
        assert "ConnectionError" in events[-1].text
        await manager.close()
    asyncio.run(scenario())


def test_bounded_queue_and_closed_signal():
    async def scenario():
        manager = SessionManager(FakeVoiceProvider(), queue_size=4)
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x00\x00"))
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x00\x00"))
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x00\x00"))
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x00\x00"))
        manager._emit(VoiceEvent(EventKind.AUDIO, audio=b"\x00\x00"))
        assert manager.dropped_audio_events == 1
        await manager.close()
        assert manager.dropped_audio_events == 2
        events = await asyncio.wait_for(_collect_until_closed(manager), timeout=1)
        assert events[-1].state is SessionState.CLOSED
    asyncio.run(scenario())


async def _collect_until_closed(manager: SessionManager):
    return [event async for event in manager.events()]


def test_provider_stream_error_fails_session():
    class ExplodingProvider(FakeVoiceProvider):
        async def events(self):
            raise ConnectionError("provider stream died")
            yield  # Make this an async generator.

    async def scenario():
        manager = SessionManager(ExplodingProvider())
        await manager.start()
        async with asyncio.timeout(1):
            async for event in manager.events():
                if event.kind is EventKind.ERROR:
                    assert "ConnectionError" in event.text
                    break
        assert manager.state is SessionState.FAILED
        await manager.close()
    asyncio.run(scenario())


def test_burst_provider_audio_uses_backpressure_not_queue_eviction():
    """A slow UI may delay output; it must not lose ordered provider PCM."""

    class BurstProvider(FakeVoiceProvider):
        async def events(self):
            for i in range(24):
                yield VoiceEvent(EventKind.AUDIO, audio=bytes((i, 0)), sample_rate=24000)
            yield VoiceEvent(EventKind.TURN_COMPLETE)
            await asyncio.Event().wait()

    async def scenario():
        manager = SessionManager(BurstProvider(), queue_size=4)
        await manager.start()
        await asyncio.sleep(0.02)  # Fill its tiny queue before consuming.
        assert manager.dropped_audio_events == 0
        received = []
        async with asyncio.timeout(2):
            async for event in manager.events():
                received.append(event)
                await asyncio.sleep(0.002)
                if event.kind is EventKind.TURN_COMPLETE:
                    break
        assert [e.audio for e in received if e.kind is EventKind.AUDIO] == [
            bytes((i, 0)) for i in range(24)
        ]
        assert manager.dropped_audio_events == 0
        assert manager.dropped_control_events == 0
        await manager.close()

    asyncio.run(scenario())
