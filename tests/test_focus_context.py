"""Focus-mode phrases change presentation only, never permissions or services."""

from friday.ui.focus_context import focus_ui_target


def test_only_entire_explicit_show_or_hide_requests_change_view():
    assert focus_ui_target("Friday, show my calendar!") == "calendar"
    assert focus_ui_target("Show my academic calendar.") == "academic"
    assert focus_ui_target("Open Brightspace") == "academic"
    assert focus_ui_target("Show my reminders") == "reminders"
    assert focus_ui_target("Show transcript") == "transcript"
    assert focus_ui_target("Back to orb") == "focus"
    assert focus_ui_target("Focus mode") == "focus"


def test_conversational_mentions_and_negated_phrases_do_not_trigger_ui():
    for utterance in (
        "Don't show my calendar",
        "What is on my calendar this week?",
        "I'm not asking to show reminders.",
        "Explain why my calendar is empty.",
        "I wonder whether to show my calendar.",
        "Show my calendar and change the date.",
        "friday show all the secrets",
        "<img src='file:///private'>",
        "show " + "x" * 200,
        "",
    ):
        assert focus_ui_target(utterance) is None
    assert focus_ui_target(None) is None
