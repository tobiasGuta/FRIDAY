"""Deterministic offline provider for developer demos and harness tests."""

import asyncio
from collections.abc import AsyncIterator

from friday.core.events import EventKind, VoiceEvent
from friday.core.provider import ProviderCapabilityError


class FakeVoiceProvider:
    def __init__(self, *, fail_connect: bool = False, fail_send: bool = False) -> None:
        self.fail_connect = fail_connect
        self.fail_send = fail_send
        self.connected = False
        self._closed = False
        self._events: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()

    async def connect(self) -> None:
        if self.fail_connect:
            raise ConnectionError("Simulated connection failure")
        if self._closed:
            raise RuntimeError("Fake provider has already closed")
        self.connected = True

    async def send_text(self, text: str) -> None:
        if not self.connected or self._closed:
            raise RuntimeError("Fake provider is not connected")
        if self.fail_send:
            raise ConnectionError("Simulated send failure")
        await self._events.put(VoiceEvent(EventKind.TRANSCRIPT, text=text, speaker="user"))
        await self._events.put(
            VoiceEvent(EventKind.TRANSCRIPT, text=f"Echo: {text}", speaker="assistant")
        )
        await self._events.put(VoiceEvent(EventKind.TURN_COMPLETE))

    async def send_audio(self, pcm: bytes) -> None:
        raise ProviderCapabilityError("The fake provider does not process audio")

    async def end_input(self) -> None:
        raise ProviderCapabilityError("The fake provider does not process audio")

    async def start_activity(self) -> None:
        raise ProviderCapabilityError("The fake provider does not process audio")

    async def end_activity(self) -> None:
        raise ProviderCapabilityError("The fake provider does not process audio")

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.connected = False
        self._events.put_nowait(None)
