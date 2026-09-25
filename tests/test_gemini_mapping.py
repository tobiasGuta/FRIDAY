from types import SimpleNamespace as Obj

from friday.core.events import EventKind
from friday.providers.gemini_live import normalize_gemini_message


def test_combined_audio_transcripts_and_turn_completion():
    content = Obj(
        input_transcription=Obj(text="hello"),
        output_transcription=Obj(text="Hi there"),
        model_turn=Obj(parts=[Obj(inline_data=Obj(data=b"\x01\x00")), Obj(text="ignored")]),
        interrupted=False,
        turn_complete=True,
    )
    events = list(normalize_gemini_message(Obj(server_content=content)))
    assert [e.kind for e in events] == [
        EventKind.TRANSCRIPT, EventKind.TRANSCRIPT, EventKind.AUDIO, EventKind.TURN_COMPLETE
    ]
    assert events[2].audio == b"\x01\x00"
    assert events[2].sample_rate == 24000
    assert "\\x01" not in repr(events[2])


def test_interrupt_goaway_are_normalized_without_executing_tools():
    message = Obj(
        server_content=Obj(
            input_transcription=None,
            output_transcription=None,
            model_turn=None,
            interrupted=True,
            turn_complete=False,
        ),
        tool_call=Obj(function_calls=[Obj(name="arbitrary_shell")]),
        go_away=Obj(time_left="5s"),
    )
    events = list(normalize_gemini_message(message))
    # Tool requests are handled at the provider/allowlist boundary, not in
    # this pure media normalizer.
    assert [e.kind for e in events] == [EventKind.INTERRUPTED, EventKind.NOTICE]


def test_generation_complete_is_not_a_final_turn_completion():
    content = Obj(
        input_transcription=None,
        output_transcription=Obj(text="Good evening."),
        model_turn=None,
        interrupted=False,
        generation_complete=True,
        turn_complete=False,
    )
    events = list(normalize_gemini_message(Obj(server_content=content)))
    assert [event.kind for event in events] == [
        EventKind.TRANSCRIPT, EventKind.GENERATION_COMPLETE
    ]
    assert EventKind.TURN_COMPLETE not in [event.kind for event in events]


def test_generation_and_turn_completion_remain_distinct_in_combined_message():
    content = Obj(
        input_transcription=None,
        output_transcription=None,
        model_turn=None,
        interrupted=False,
        generation_complete=True,
        turn_complete=True,
    )
    events = list(normalize_gemini_message(Obj(server_content=content)))
    assert [event.kind for event in events] == [
        EventKind.GENERATION_COMPLETE, EventKind.TURN_COMPLETE
    ]
