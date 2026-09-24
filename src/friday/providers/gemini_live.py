"""Gemini Live adapter. This is the only module coupled to google-genai."""

import asyncio
from collections import abc
from typing import Any

from friday.config import Settings
from friday.core.events import EventKind, VoiceEvent
from friday.core.provider import ProviderCapabilityError
from friday.tools.builtins import build_builtin_registry
from friday.tools.search_grounding import extract_search_grounding

FRIDAY_INSTRUCTION = (
    "You are FRIDAY, Tobias's personal AI assistant. Speak naturally and concisely. "
    "For the current date or time, call get_local_time and use the returned computer clock "
    "value, rather than guessing. The tool reads the computer's configured local timezone, "
    "not geographic location; do not infer the computer's city or answer other cities' times "
    "from that clock alone. You can converse but cannot control the computer or retain "
    "long-term memories. Never claim an action occurred unless the application confirms it."
)


WEB_SEARCH_INSTRUCTION = (
    " For recent or changing facts, use Google Search when available. If grounding "
    "does not provide usable sources, say that you could not verify the information "
    "instead of making up citations. Speak concisely; references appear separately "
    "in the FRIDAY terminal. Do not claim that search ran unless it actually did."
)


def normalize_gemini_message(
    message: Any, *, output_sample_rate: int = 24000
) -> abc.Iterator[VoiceEvent]:
    """Map SDK responses to core events without requiring the SDK during unit tests."""
    content = getattr(message, "server_content", None)
    if content is not None:
        grounding = extract_search_grounding(content)
        if grounding is not None:
            yield grounding
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
    if getattr(message, "go_away", None):
        yield VoiceEvent(EventKind.NOTICE, text="Gemini server is closing this connection")


class GeminiLiveProvider:
    """Direct audio-to-audio provider; no microphone or speaker ownership."""

    def __init__(
        self,
        settings: Settings,
        *,
        manual_activity: bool = False,
        enable_local_clock: bool = False,
        enable_web_search: bool = False,
    ) -> None:
        self.settings = settings
        self._manual_activity = manual_activity
        self._tool_registry = build_builtin_registry(enable_local_clock=enable_local_clock)
        self._enable_web_search = enable_web_search
        self._activity_open = False
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
        declarations = self._tool_registry.declarations()
        tools = []
        if self._enable_web_search:
            tools.append({"google_search": {}})
        if declarations:
            tools.append({"function_declarations": declarations})
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=(
                FRIDAY_INSTRUCTION
                + (WEB_SEARCH_INSTRUCTION if self._enable_web_search else "")
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.settings.voice)
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            **({"tools": tools} if tools else {}),
            **(
                {
                    "realtime_input_config": types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            disabled=True
                        )
                    )
                }
                if self._manual_activity
                else {}
            ),
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
        if self._manual_activity and not self._activity_open:
            raise ProviderCapabilityError("Cannot send audio outside a manual voice turn")
        from google.genai import types

        await self._require_session().send_realtime_input(
            audio=types.Blob(
                data=pcm, mime_type=f"audio/pcm;rate={self.settings.input_sample_rate}"
            )
        )

    async def end_input(self) -> None:
        if self._manual_activity:
            raise ProviderCapabilityError("Manual voice turns must use end_activity")
        await self._require_session().send_realtime_input(audio_stream_end=True)

    async def start_activity(self) -> None:
        if not self._manual_activity or self._activity_open:
            raise ProviderCapabilityError("Manual activity start unavailable or already active")
        from google.genai import types

        await self._require_session().send_realtime_input(activity_start=types.ActivityStart())
        self._activity_open = True

    async def end_activity(self) -> None:
        if not self._manual_activity or not self._activity_open:
            raise ProviderCapabilityError("No manual voice activity is active")
        from google.genai import types

        await self._require_session().send_realtime_input(activity_end=types.ActivityEnd())
        self._activity_open = False

    async def events(self) -> abc.AsyncIterator[VoiceEvent]:
        session = self._require_session()
        # A function call can emit an intermediate turn_complete before Gemini
        # resumes and speaks the answer. That is NOT the end of the user's turn.
        awaiting_tool_followup = False
        # The SDK receive() iterator can end after a completed model turn;
        # a persistent conversation must call receive() again for later turns.
        while not self._closed:
            had_message = False
            async for message in session.receive():
                had_message = True
                tool_call = getattr(message, "tool_call", None)
                if tool_call is not None:
                    # Live API requires an explicit FunctionResponse. No arbitrary
                    # Python function lookup, shell execution or dynamic imports.
                    from google.genai import types

                    responses = []
                    for call in getattr(tool_call, "function_calls", None) or []:
                        name = getattr(call, "name", None)
                        result = self._tool_registry.execute(name, getattr(call, "args", None))
                        responses.append(
                            types.FunctionResponse(
                                id=call.id, name=name, response={"result": result}
                            )
                        )
                        yield VoiceEvent(
                            EventKind.NOTICE,
                            text=self._tool_registry.notice_for(name, result),
                        )
                    if responses:
                        # Set this before yielding any events from the same SDK
                        # message: a tool call may carry an intermediate completion.
                        awaiting_tool_followup = True
                        await session.send_tool_response(function_responses=responses)
                for event in normalize_gemini_message(
                    message, output_sample_rate=self.settings.output_sample_rate
                ):
                    if event.kind is EventKind.TURN_COMPLETE and awaiting_tool_followup:
                        # Do not tell the UI to reopen the microphone while a
                        # tool-assisted spoken answer has not arrived yet.
                        continue
                    if (
                        tool_call is None
                        and (
                            event.kind is EventKind.AUDIO
                            or (
                                event.kind is EventKind.TRANSCRIPT
                                and event.speaker == "assistant"
                            )
                        )
                    ):
                        awaiting_tool_followup = False
                    if event.kind is EventKind.INTERRUPTED:
                        awaiting_tool_followup = False
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
