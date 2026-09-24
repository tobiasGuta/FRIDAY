"""Mock SDK integration test: never contacts Google or requires an API key."""

import asyncio
import sys
from types import ModuleType
from types import SimpleNamespace as Obj

import pytest

from friday.config import Settings
from friday.core.events import EventKind
from friday.core.provider import ProviderCapabilityError
from friday.providers.gemini_live import GeminiLiveProvider


class MockSession:
    def __init__(self):
        self.sent = []
        self.receives = 0

    async def send_realtime_input(self, **kwargs):
        self.sent.append(kwargs)

    async def receive(self):
        self.receives += 1
        yield Obj(
            server_content=Obj(
                input_transcription=None,
                output_transcription=Obj(text="Hello from mock Gemini"),
                model_turn=Obj(parts=[Obj(inline_data=Obj(data=b"\x01\x00"))]),
                interrupted=False,
                turn_complete=True,
            )
        )


class MockContext:
    def __init__(self, session):
        self.session = session
        self.exited = False

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        self.exited = True


class MockClient:
    def __init__(self, *, api_key):
        assert api_key == "mock-key"
        self.session = MockSession()
        self.context = MockContext(self.session)
        self.live = Obj(connect=self.connect)
        self.aio = Obj(live=self.live, aclose=self.aclose)
        self.closed = False
        self.async_closed = False
        self.config = None

    def connect(self, *, model, config):
        assert model == "gemini-3.8-live"
        self.config = config
        return self.context

    async def aclose(self):
        self.async_closed = True

    def close(self):
        self.closed = True


def test_adapter_connect_send_receive_and_close(monkeypatch):
    client_holder = {}

    def client_factory(**kwargs):
        client = MockClient(**kwargs)
        client_holder["client"] = client
        return client

    google = ModuleType("google")
    google.__path__ = []
    genai = ModuleType("google.genai")
    genai.Client = client_factory
    types = ModuleType("google.genai.types")
    for name in (
        "LiveConnectConfig", "SpeechConfig", "VoiceConfig", "PrebuiltVoiceConfig",
        "AudioTranscriptionConfig", "Blob", "RealtimeInputConfig",
        "AutomaticActivityDetection", "ActivityStart", "ActivityEnd"
    ):
        setattr(types, name, lambda **kwargs: Obj(**kwargs))
    types.Modality = Obj(AUDIO="AUDIO")
    genai.types = types
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)

    async def scenario():
        settings = Settings(_env_file=None, GEMINI_API_KEY="mock-key")
        adapter = GeminiLiveProvider(settings)
        await adapter.connect()
        await adapter.send_text("Hello")
        await adapter.send_audio(b"\x01\x00")
        await adapter.end_input()
        stream = adapter.events()
        events = []
        for _ in range(3):
            events.append(await asyncio.wait_for(anext(stream), timeout=1))
        await stream.aclose()
        assert [e.kind for e in events] == [
            EventKind.TRANSCRIPT, EventKind.AUDIO, EventKind.TURN_COMPLETE
        ]
        client = client_holder["client"]
        assert client.config.response_modalities == ["AUDIO"]
        assert client.session.sent[0] == {"text": "Hello"}
        assert client.session.sent[1]["audio"].mime_type == "audio/pcm;rate=16000"
        assert client.session.sent[2] == {"audio_stream_end": True}
        await adapter.close()
        await adapter.close()
        assert client.context.exited and client.closed and client.async_closed
        manual = GeminiLiveProvider(settings, manual_activity=True)
        await manual.connect()
        manual_client = client_holder["client"]
        assert manual_client.config.realtime_input_config.automatic_activity_detection.disabled
        assert not hasattr(client.config, "realtime_input_config")
        with pytest.raises(ProviderCapabilityError, match="outside a manual"):
            await manual.send_audio(b"\x02\x00")
        await manual.start_activity()
        with pytest.raises(ProviderCapabilityError, match="already active"):
            await manual.start_activity()
        await manual.send_audio(b"\x02\x00")
        await manual.end_activity()
        with pytest.raises(ProviderCapabilityError, match="No manual voice"):
            await manual.end_activity()
        with pytest.raises(ProviderCapabilityError, match="end_activity"):
            await manual.end_input()
        assert [list(item) for item in manual_client.session.sent] == [
            ["activity_start"], ["audio"], ["activity_end"]
        ]
        assert manual_client.session.sent[1]["audio"].data == b"\x02\x00"
        await manual.close()

    asyncio.run(scenario())
