"""Offline native Search grounding tests: never connect or open a real browser."""

import asyncio
from types import SimpleNamespace as Obj

from friday.config import Settings
from friday.core.events import EventKind, SearchSource, VoiceEvent
from friday.core.session import SessionManager
from friday.providers.fake import FakeVoiceProvider
from friday.providers.gemini_live import GeminiLiveProvider, normalize_gemini_message
from friday.tools.search_grounding import SearchPreview, extract_search_grounding
from friday.ui.cli import _voice_events, build_parser
from test_clock_live import install_mock_sdk
from test_voice_cli import RecordingSpeaker


def _content(*, metadata=None, transcript=None, complete=False):
    return Obj(
        grounding_metadata=metadata,
        input_transcription=None,
        output_transcription=Obj(text=transcript) if transcript else None,
        model_turn=None,
        interrupted=False,
        turn_complete=complete,
    )


def test_grounding_maps_sdk_fields_deduplicates_and_precedes_completion():
    entry = Obj(rendered_content='<a href="https://google.com">Search</a>')
    metadata = Obj(
        grounding_chunks=[
            Obj(web=Obj(uri="https://example.com/one", title="First")),
            Obj(web=Obj(uri="https://example.com/one", title="Repeated")),
            Obj(web=Obj(uri="javascript:alert(1)", title="Bad")),
            Obj(web=Obj(uri="https://example.com/two", title="Second")),
        ],
        search_entry_point=entry,
    )
    event = extract_search_grounding(_content(metadata=metadata))
    assert event.kind is EventKind.GROUNDING
    assert event.sources == (
        SearchSource("First", "https://example.com/one"),
        SearchSource("Second", "https://example.com/two"),
    )
    assert event.search_suggestions_html == entry.rendered_content
    assert "google.com" not in repr(event)  # Vendor markup never reaches event diagnostics.
    events = list(normalize_gemini_message(Obj(server_content=_content(
        metadata=metadata, transcript="Current answer", complete=True
    ))))
    assert [e.kind for e in events] == [
        EventKind.GROUNDING, EventKind.TRANSCRIPT, EventKind.TURN_COMPLETE
    ]


def test_absent_and_invalid_grounding_is_not_fabricated():
    assert extract_search_grounding(_content()) is None
    assert extract_search_grounding(_content(metadata=Obj(
        grounding_chunks=[], search_entry_point=None
    ))) is None
    event = extract_search_grounding(_content(metadata={
        "grounding_chunks": [
            {"web": {"uri": "http://not-https.invalid", "title": "a"}},
            {"web": {"uri": "https://example.com/\nspoof", "title": "a"}},
            {"web": {"uri": "https://example.com/good", "title": "Bad\nTitle"}},
            {"web": {"uri": "https://user:pass@example.com/", "title": "bad"}},
        ],
        "search_entry_point": None,
    }))
    assert event.sources == (SearchSource("Bad Title", "https://example.com/good"),)
    assert event.search_suggestions_html is None


def test_search_preview_escapes_untrusted_text_and_uses_google_widget(tmp_path):
    seen = []
    preview = SearchPreview(tmp_path, open_url=lambda url: seen.append(url) or True)
    markup = '<style>a{color:blue}</style><a href="https://www.google.com/search?q=test">More</a>'
    assert preview.show(
        "Answer <script>alert(1)</script>",
        (SearchSource('<img src=x onerror="oops">', 'https://example.com/?a=1&b=2'),),
        markup,
    ) is True
    assert len(seen) == 1 and seen[0].startswith("file:")
    page = next(tmp_path.iterdir()).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "Answer &lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;style&gt;" in page
    assert 'sandbox="allow-popups allow-popups-to-escape-sandbox"' in page
    assert "allow-scripts" not in page
    assert 'href="https://example.com/?a=1&amp;b=2"' in page
    assert "&lt;img src=x" in page


def test_google_search_is_explicit_opt_in_and_coexists_with_clock(monkeypatch):
    session, clients = install_mock_sdk(monkeypatch, [])

    async def scenario():
        settings = Settings(_env_file=None, GEMINI_API_KEY="mock-key")
        ordinary = GeminiLiveProvider(settings, manual_activity=False, enable_local_clock=True)
        await ordinary.connect()
        assert clients[0].config.tools == [
            {"function_declarations": clients[0].config.tools[0]["function_declarations"]}
        ]
        await ordinary.close()
        grounded = GeminiLiveProvider(
            settings, manual_activity=False, enable_local_clock=True,
            enable_web_search=True,
        )
        await grounded.connect()
        assert clients[1].config.tools[0] == {"google_search": {}}
        assert [d["name"] for d in clients[1].config.tools[1]["function_declarations"]] == [
            "get_local_time"
        ]
        assert "Google Search" in clients[1].config.system_instruction
        assert "Google Search" not in clients[0].config.system_instruction
        await grounded.close()

        async def grounded_receive():
            yield Obj(tool_call=None, server_content=_content(
                metadata=Obj(grounding_chunks=[Obj(web=Obj(
                    uri="https://news.example.com/a", title="Example News"
                ))], search_entry_point=None), transcript="Recent update", complete=True
            ))

        session.receive = grounded_receive
        third = GeminiLiveProvider(settings, enable_web_search=True)
        await third.connect()
        try:
            observed = []
            async with asyncio.timeout(1):
                async for event in third.events():
                    observed.append(event)
                    if event.kind is EventKind.TURN_COMPLETE:
                        break
            assert [e.kind for e in observed] == [
                EventKind.GROUNDING, EventKind.TRANSCRIPT, EventKind.TURN_COMPLETE
            ]
            assert observed[0].sources[0].title == "Example News"
        finally:
            await third.close()

    asyncio.run(scenario())


def test_source_urls_display_once_after_answer_and_reset_between_turns(capsys):
    async def scenario():
        manager = SessionManager(FakeVoiceProvider())
        await manager.start()
        speaker = RecordingSpeaker()
        receiver = asyncio.create_task(_voice_events(manager, speaker))
        source = SearchSource("Example", "https://example.com/item")
        manager._emit(VoiceEvent(EventKind.GROUNDING, sources=(source, source)))
        manager._emit(
            VoiceEvent(EventKind.TRANSCRIPT, text="A grounded answer.", speaker="assistant")
        )
        manager._emit(VoiceEvent(EventKind.TURN_COMPLETE))
        manager._emit(
            VoiceEvent(EventKind.TRANSCRIPT, text="A non-web answer.", speaker="assistant")
        )
        manager._emit(VoiceEvent(EventKind.TURN_COMPLETE))
        manager._emit(VoiceEvent(EventKind.ERROR, text="test complete"))
        assert await asyncio.wait_for(receiver, 1) is False
        await manager.close()

    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert output.count("https://example.com/item") == 1
    assert output.count("Google Search grounding returned") == 1
    assert output.count("FRIDAY turn complete") == 2
    assert output.index("assistant: A grounded answer.") < output.index("Google Search grounding")


def test_opt_in_cli_flag_preserves_old_default_and_prompt():
    old = build_parser().parse_args(["talk", "--input-device", "1"])
    opt_in = build_parser().parse_args(["talk", "--input-device", "1", "--web"])
    assert old.web is False
    assert opt_in.web is True
    assert opt_in.output_device is None
