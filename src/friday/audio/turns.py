"""An explicit press-to-talk turn over the provider-neutral session contract."""

from __future__ import annotations

import asyncio
from typing import Protocol

from friday.core.session import SessionManager


class AudioInput(Protocol):
    dropped_chunks: int

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def chunks(self): ...


class AudioOutput(Protocol):
    def flush(self) -> None: ...


class VoiceTurns:
    def __init__(self, manager: SessionManager, microphone: AudioInput, speaker: AudioOutput):
        self.manager = manager
        self.microphone = microphone
        self.speaker = speaker
        self._sender: asyncio.Task[None] | None = None
        self.recording = False
        self.awaiting_response = False

    async def start(self) -> None:
        if self.recording:
            raise RuntimeError("Already recording")
        if self.awaiting_response:
            raise RuntimeError("Previous voice turn is still responding")
        # Explicit barge-in: clear buffered speech before starting the next turn.
        self.speaker.flush()
        self.microphone.start()
        self.recording = True
        self._sender = asyncio.create_task(self._send_frames(), name="friday-microphone-sender")

    async def _send_frames(self) -> None:
        async for frame in self.microphone.chunks():
            await self.manager.send_audio(frame)

    async def stop(self) -> None:
        if not self.recording:
            return
        self.recording = False
        self.microphone.stop()
        sender, self._sender = self._sender, None
        if sender is not None:
            # A failed sender must not silently signal a successfully delivered turn.
            await sender
        # Do not admit another recording until Gemini finishes and playback drains.
        self.awaiting_response = True
        await self.manager.end_input()

    def response_finished(self) -> None:
        """Called after provider completion/interruption and local playback drain."""
        self.awaiting_response = False

    async def close(self) -> None:
        self.recording = False
        self.awaiting_response = False
        if self._sender is not None:
            self._sender.cancel()
            try:
                await self._sender
            except asyncio.CancelledError:
                pass
            self._sender = None
        self.microphone.stop()
