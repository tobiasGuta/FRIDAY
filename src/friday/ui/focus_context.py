"""Conservative, presentation-only voice commands for the Focus UI.

These phrases change only the local Qt view. They never execute an assistant
tool, contact a service, or bypass human approval.
"""

import re

_TARGETS = {
    "show academic": "academic",
    "show my academic calendar": "academic",
    "open academic": "academic",
    "show brightspace": "academic",
    "open brightspace": "academic",
    "show my brightspace calendar": "academic",
    "show reminders": "reminders",
    "show my reminders": "reminders",
    "open reminders": "reminders",
    "open my reminders": "reminders",
    "show calendar": "calendar",
    "show my calendar": "calendar",
    "open calendar": "calendar",
    "open my calendar": "calendar",
    "show transcript": "transcript",
    "open transcript": "transcript",
    "hide panel": "focus",
    "close panel": "focus",
    "back to orb": "focus",
    "focus mode": "focus",
}


def focus_ui_target(utterance: str) -> str | None:
    """Match an entire clear command, never a keyword embedded in conversation.

    Live transcription can be partial; partial words are intentionally ignored.
    The UI also has visible panel controls when speech recognition differs.
    """
    if not isinstance(utterance, str) or len(utterance) > 120:
        return None
    normalized = re.sub(r"[^a-z0-9\s]", "", utterance.casefold())
    normalized = " ".join(normalized.split())
    if normalized.startswith("friday "):
        normalized = normalized.removeprefix("friday ")
    return _TARGETS.get(normalized)
