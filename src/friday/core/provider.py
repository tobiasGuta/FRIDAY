"""Provider boundary. Implementations may use direct audio or STT -> LLM -> TTS."""

from collections.abc import AsyncIterator
from typing import Protocol

from friday.core.events import VoiceEvent


class ProviderCapabilityError(RuntimeError):
    """The selected provider does not implement the requested operation."""


class VoiceProvider(Protocol):
    async def connect(self) -> None: ...

    async def send_text(self, text: str) -> None: ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def end_input(self) -> None: ...

    def events(self) -> AsyncIterator[VoiceEvent]: ...

    async def close(self) -> None: ...
