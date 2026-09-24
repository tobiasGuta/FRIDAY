"""Mock Live API function calls: prove typed allowlist and response round trip."""

import asyncio
import sys
from types import ModuleType
from types import SimpleNamespace as Obj

import pytest

from friday.config import Settings
from friday.core.events import EventKind
from friday.providers.gemini_live import GeminiLiveProvider


class MockLiveSession:
    def __init__(self, calls):
        self.calls = calls
        self.tool_responses = []
        self.receive_calls = 0

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)

    async def receive(self):
        self.receive_calls += 1
        if self.receive_calls == 1:
            yield Obj(tool_call=Obj(function_calls=self.calls), server_content=None)
            # A synchronous Live tool call pauses generation until a response arrives.
            assert len(self.tool_responses) == len(self.calls)
            yield Obj(
                tool_call=None,
                server_content=Obj(
                    input_transcription=None,
                    output_transcription=Obj(text="It is 9:53 PM computer local time."),
                    model_turn=None,
                    interrupted=False,
                    turn_complete=True,
                ),
            )
        else:
            await asyncio.sleep(3600)


class MockContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        return None


class MockClient:
    def __init__(self, session, **_):
        self.session = session
        self.config = None
        self.aio = Obj(live=Obj(connect=self.connect), aclose=self.aclose)

    def connect(self, *, model, config):
        self.config = config
        return MockContext(self.session)

    async def aclose(self):
        return None

    def close(self):
        return None


def install_mock_sdk(monkeypatch, calls):
    session = MockLiveSession(calls)
    clients = []

    def create_client(**kwargs):
        client = MockClient(session, **kwargs)
        clients.append(client)
        return client

    google = ModuleType("google")
    google.__path__ = []
    genai = ModuleType("google.genai")
    genai.Client = create_client
    types = ModuleType("google.genai.types")
    for name in (
        "LiveConnectConfig",
        "SpeechConfig",
        "VoiceConfig",
        "PrebuiltVoiceConfig",
        "AudioTranscriptionConfig",
        "FunctionResponse",
    ):
        setattr(types, name, lambda **kwargs: Obj(**kwargs))
    types.Modality = Obj(AUDIO="AUDIO")
    genai.types = types
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)
    return session, clients


def test_live_clock_tool_roundtrip_and_unknown_calls_are_rejected(monkeypatch):
    calls = [
        Obj(id="clock-1", name="get_local_time", args={}),
        Obj(id="bad-2", name="run_shell", args={"cmd": "whoami"}),
        Obj(id="bad-3", name="get_local_time", args={"timezone": "Mars"}),
    ]
    session, clients = install_mock_sdk(monkeypatch, calls)
    clock_calls = []

    def read_clock(*, now=None):
        clock_calls.append(True)
        return {
            "local_datetime": "2026-09-23T21:53:12-04:00",
            "date": "2026-09-23",
            "time_12h": "9:53:12 PM",
            "time_24h": "21:53:12",
            "weekday": "Wednesday",
            "timezone_name": "EDT",
            "utc_offset": "-04:00",
            "source": "computer_local_clock",
        }

    monkeypatch.setattr("friday.tools.local_clock.read_local_clock", read_clock)

    async def scenario():
        settings = Settings(_env_file=None, GEMINI_API_KEY="mock-key")
        adapter = GeminiLiveProvider(settings, enable_local_clock=True)
        await adapter.connect()
        try:
            decls = clients[0].config.tools[0]["function_declarations"]
            assert [item["name"] for item in decls] == ["get_local_time"]
            received = []
            async with asyncio.timeout(1):
                async for event in adapter.events():
                    received.append(event)
                    if event.kind is EventKind.TURN_COMPLETE:
                        break
            assert [e.kind for e in received] == [
                EventKind.NOTICE, EventKind.NOTICE, EventKind.NOTICE,
                EventKind.TRANSCRIPT, EventKind.TURN_COMPLETE,
            ]
            assert [r.id for r in session.tool_responses] == [
                "clock-1", "bad-2", "bad-3"
            ]
            assert all(
                r.name == call.name
                for r, call in zip(session.tool_responses, calls, strict=True)
            )
            results = [r.response["result"] for r in session.tool_responses]
            assert results[0]["time_12h"] == "9:53:12 PM"
            assert results[0]["source"] == "computer_local_clock"
            assert results[1] == {"status": "error", "error": "unknown_tool"}
            assert results[2] == {"status": "error", "error": "invalid_arguments"}
            assert clock_calls == [True]
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_no_clock_enabled_rejects_even_named_clock_without_reading_it(monkeypatch):
    session, clients = install_mock_sdk(
        monkeypatch, [Obj(id="a", name="get_local_time", args={})]
    )

    def fail_clock(*, now=None):
        raise AssertionError("Clock must not execute when disabled")

    monkeypatch.setattr("friday.tools.local_clock.read_local_clock", fail_clock)

    async def scenario():
        adapter = GeminiLiveProvider(Settings(_env_file=None, GEMINI_API_KEY="mock-key"))
        await adapter.connect()
        try:
            assert not hasattr(clients[0].config, "tools")
            async with asyncio.timeout(1):
                async for event in adapter.events():
                    if event.kind is EventKind.TURN_COMPLETE:
                        break
            assert session.tool_responses[0].response["result"] == {
                "status": "error", "error": "unknown_tool"
            }
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("completion_in_tool_message", [False, True])
def test_clock_tool_intermediate_completion_does_not_finish_spoken_turn(
    monkeypatch, completion_in_tool_message
):
    """A tool-step completion must not release push-to-talk before spoken answer."""
    call = Obj(id="clock-1", name="get_local_time", args={})
    session, _ = install_mock_sdk(monkeypatch, [call])

    def content(*, transcript=None, audio=None, complete=False):
        return Obj(
            input_transcription=None,
            output_transcription=Obj(text=transcript) if transcript else None,
            model_turn=(
                Obj(parts=[Obj(inline_data=Obj(data=audio))]) if audio is not None else None
            ),
            interrupted=False,
            turn_complete=complete,
        )

    async def receive():
        # Some server sequences carry the tool completion in the same message;
        # others emit it just after the function response is submitted.
        yield Obj(
            tool_call=Obj(function_calls=[call]),
            server_content=content(complete=completion_in_tool_message),
        )
        assert len(session.tool_responses) == 1
        if not completion_in_tool_message:
            yield Obj(tool_call=None, server_content=content(complete=True))
        yield Obj(tool_call=None, server_content=content(transcript="It is 10:10 PM."))
        yield Obj(tool_call=None, server_content=content(audio=b"\x01\x00"))
        yield Obj(tool_call=None, server_content=content(complete=True))

    session.receive = receive

    async def scenario():
        adapter = GeminiLiveProvider(
            Settings(_env_file=None, GEMINI_API_KEY="mock-key"), enable_local_clock=True
        )
        await adapter.connect()
        try:
            observed = []
            async with asyncio.timeout(1):
                async for event in adapter.events():
                    observed.append(event)
                    if event.kind is EventKind.TURN_COMPLETE:
                        break
            assert [event.kind for event in observed] == [
                EventKind.NOTICE,
                EventKind.TRANSCRIPT,
                EventKind.AUDIO,
                EventKind.TURN_COMPLETE,
            ]
            assert observed[1].text == "It is 10:10 PM."
            assert observed[2].audio == b"\x01\x00"
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_voice_defaults_to_english_even_when_auto_transcription_guesses_spanish(
    monkeypatch, tmp_path,
):
    """Language preference applies to both ordinary and reminder-enabled Live sessions."""
    from friday.schedule import ScheduleStore
    from friday.voice_reminders import VoiceReminderApproval

    _session, clients = install_mock_sdk(monkeypatch, [])

    async def scenario():
        settings = Settings(_env_file=None, GEMINI_API_KEY="mock-key")
        ordinary = GeminiLiveProvider(settings, enable_local_clock=True)
        await ordinary.connect()
        try:
            instruction = clients[0].config.system_instruction
            assert "American English" in instruction
            assert "automatic speech" in instruction
            assert "Never switch to Spanish" in instruction
            assert "ask in English for a repeat" in instruction
        finally:
            await ordinary.close()

        approved = VoiceReminderApproval(ScheduleStore(tmp_path / "schedules.sqlite3"))
        reminder_session = GeminiLiveProvider(
            settings, enable_local_clock=True, reminder_approval=approved
        )
        await reminder_session.connect()
        try:
            instruction = clients[1].config.system_instruction
            assert "American English" in instruction
            assert "Never switch to Spanish" in instruction
            assert "get_reminders" in instruction
            assert "application controls approval" in instruction
        finally:
            await reminder_session.close()

    asyncio.run(scenario())


def test_live_input_language_hint_and_auto_mode_do_not_modify_audio_output(monkeypatch):
    """Check Live setup, not merely the wording of its system instruction."""
    _session, clients = install_mock_sdk(monkeypatch, [])

    async def scenario():
        settings = Settings(_env_file=None, GEMINI_API_KEY="mock-key")
        english = GeminiLiveProvider(
            settings, manual_activity=False, enable_local_clock=True,
            input_language="en-US",
        )
        await english.connect()
        try:
            config = clients[0].config
            assert config.input_audio_transcription.language_codes == ["en-US"]
            assert config.response_modalities == ["AUDIO"]
            assert "American English" in config.system_instruction
        finally:
            await english.close()

        automatic = GeminiLiveProvider(settings, input_language=None)
        await automatic.connect()
        try:
            config = clients[1].config
            assert not hasattr(config.input_audio_transcription, "language_codes")
            assert config.response_modalities == ["AUDIO"]
        finally:
            await automatic.close()

    asyncio.run(scenario())
    with pytest.raises(ValueError, match="Unsupported input language"):
        GeminiLiveProvider(Settings(_env_file=None), input_language="es-ES")


def test_installed_genai_sdk_accepts_english_transcription_hint():
    """Catch SDK schema regressions without making a paid Live connection."""
    from google.genai import types

    transcript = types.AudioTranscriptionConfig(language_codes=["en-US"])
    assert transcript.language_codes == ["en-US"]
