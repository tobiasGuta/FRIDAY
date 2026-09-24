"""Gemini Live adapter. This is the only module coupled to google-genai."""

import asyncio
from collections import abc
from typing import Any

from friday.config import Settings
from friday.core.events import EventKind, VoiceEvent

FRIDAY_INSTRUCTION = (
    "You are FRIDAY, Tobias's personal AI assistant. Speak naturally and concisely. "
    "You can converse but cannot yet control the computer, retain long-term memories, "
    "or execute tools. Never claim an action occurred unless the application confirms it."
)


def normalize_gemini_message(
    message: Any, *, output_sample_rate: int = 24000
) -> abc.Iterator[VoiceEvent]:
    """Map SDK responses to core events without requiring the SDK during unit tests."""
    content = getattr(message, "server_content", None)
    if content is not None:
        incoming = getattr(content, "input_transcription", None)
        if incoming is not None and getattr(incoming, "text", None):
            yield VoiceEvent(EventKind.TRANSCRIPT, text=incoming.text, speaker="user")
        outgoing = getattr(content, "output_transcription", None)
        if outgoing is not None and getattr(outgoing, "text", None):
            yield VoiceEvent(EventKind.TRANSCRIPT, text=outgoing.text, speaker="assistant")
        turn = getattr(content, "model_turn", None)
        if turn is not None:
            for part in getattr(turn, "parts", None) or []:
                inline_data = getattr(part, "inline_data", None)
                if inline_data is not None and getattr(inline_data, "data", None):
                    yield VoiceEvent(
                        EventKind.AUDIO,
                        audio=inline_data.data,
                        sample_rate=output_sample_rate,
                        speaker="assistant",
                    )
        if getattr(content, "interrupted", False):
            yield VoiceEvent(EventKind.INTERRUPTED)
        if getattr(content, "turn_complete", False):
            yield VoiceEvent(EventKind.TURN_COMPLETE)
    if getattr(message, "tool_call", None):
        # v0.1 registers no tools. Never execute an unexpected model request.
        yield VoiceEvent(EventKind.NOTICE, text="Unexpected tool call ignored; tools disabled")
    if getattr(message, "go_away", None):
        yield VoiceEvent(EventKind.NOTICE, text="Gemini server is closing this connection")


class GeminiLiveProvider:
    """Direct audio-to-audio provider; no microphone or speaker ownership."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None
        self._context: Any = None
        self._session: Any = None
        self._closed = False

    async def connect(self) -> None:
        if self._session is not None or self._closed:
            raise RuntimeError("Gemini provider cannot be connected twice")
        key = self.settings.require_gemini_key()
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("Install Gemini support: pip install -e '.[gemini]'") from exc

        self._client = genai.Client(api_key=key)
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=FRIDAY_INSTRUCTION,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.settings.voice)
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        self._context = self._client.aio.live.connect(model=self.settings.model, config=config)
        try:
            self._session = await self._context.__aenter__()
        except BaseException:
            await self._client.aio.aclose()
            self._client.close()
            self._client = None
            self._context = None
            raise

    def _require_session(self) -> Any:
        if self._session is None or self._closed:
            raise RuntimeError("Gemini session is not connected")
        return self._session

    async def send_text(self, text: str) -> None:
        await self._require_session().send_realtime_input(text=text)

    async def send_audio(self, pcm: bytes) -> None:
        from google.genai import types

        await self._require_session().send_realtime_input(
            audio=types.Blob(
                data=pcm, mime_type=f"audio/pcm;rate={self.settings.input_sample_rate}"
            )
        )

    async def end_input(self) -> None:
        await self._require_session().send_realtime_input(audio_stream_end=True)

    async def events(self) -> abc.AsyncIterator[VoiceEvent]:
        session = self._require_session()
        # The SDK receive() iterator can end after a completed model turn;
        # a persistent conversation must call receive() again for later turns.
        while not self._closed:
            had_message = False
            async for message in session.receive():
                had_message = True
                for event in normalize_gemini_message(
                    message, output_sample_rate=self.settings.output_sample_rate
                ):
                    yield event
            if not had_message:
                # Avoid a busy loop if an SDK release returns an empty turn.
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session = None
        try:
            if self._context is not None:
                await self._context.__aexit__(None, None, None)
        finally:
            self._context = None
            if self._client is not None:
                try:
                    await self._client.aio.aclose()
                finally:
                    self._client.close()
                    self._client = None
