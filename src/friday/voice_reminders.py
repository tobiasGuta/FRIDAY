"""Ephemeral, app-owned reminder drafts with explicit human approval.

Gemini may draft a reminder, but cannot write schedules or authorize a draft.
Only the host handles approval from an exact user transcript in a later turn
or an explicit terminal command. No draft survives a voice-session restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from pydantic import Field

from friday.schedule import MAX_TEXT, ScheduleStore, parse_due
from friday.tools.registry import ToolArguments, ToolRegistry, ToolSpec

DRAFT_TOOL_NAME = "draft_reminder"
APPROVAL_PHRASES = frozenset({
    "yes",
    "yes please",
    "yes create it",
    "yes create that reminder",
    "yes create the reminder",
    "approve reminder",
    "approve the reminder",
    "confirm reminder",
    "confirm the reminder",
})
REJECTION_PHRASES = frozenset({
    "no",
    "no thanks",
    "no thank you",
    "cancel reminder",
    "cancel the reminder",
    "reject reminder",
    "reject the reminder",
})


class DraftReminderArguments(ToolArguments):
    text: str = Field(
        min_length=1, max_length=MAX_TEXT,
        description="Brief reminder text, no newlines (maximum 200 characters).",
    )
    at: str = Field(
        min_length=20, max_length=40,
        description=(
            "Future ISO 8601 date and time with explicit numeric UTC offset, "
            "for example 2026-09-27T19:00:00-04:00. Use get_local_time first "
            "for relative dates. Ask for clarification if the time is ambiguous."
        ),
    )


@dataclass(frozen=True, slots=True)
class ReminderDraft:
    token: str
    text: str
    at: str
    created_at: float


class VoiceReminderApproval:
    """One pending proposal per voice session, never a model-authorized write."""

    def __init__(self, store: ScheduleStore, *, ttl_seconds: float = 300.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Draft TTL must be positive")
        self.store = store
        self.ttl_seconds = ttl_seconds
        self._pending: ReminderDraft | None = None
        self._eligible_token: str | None = None
        self._transcript_parts: list[str] = []
        self._transcript_overflow = False

    def pending(self) -> ReminderDraft | None:
        draft = self._pending
        if draft is not None and monotonic() - draft.created_at >= self.ttl_seconds:
            self._pending = None
            self._eligible_token = None
            return None
        return draft

    def propose(self, arguments: DraftReminderArguments) -> dict[str, str]:
        if self.pending() is not None:
            return {"status": "error", "error": "pending_approval"}
        text = arguments.text.strip()
        if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
            return {"status": "error", "error": "invalid_text"}
        try:
            parse_due(arguments.at)
        except (TypeError, ValueError):
            return {"status": "error", "error": "invalid_due_time"}
        draft = ReminderDraft(uuid4().hex, text, arguments.at, monotonic())
        self._pending = draft
        return {
            "status": "ok",
            "state": "pending_approval",
            "text": draft.text,
            "at": draft.at,
            "instruction": (
                "This is only a draft. Ask the user to confirm in a separate voice turn. "
                "The app alone will save it after explicit user approval."
            ),
        }

    def begin_turn(self) -> None:
        """Freeze eligibility BEFORE recording: no proposal and approval in one turn."""
        draft = self.pending()
        self._eligible_token = draft.token if draft is not None else None
        self._transcript_parts.clear()
        self._transcript_overflow = False

    def hear_user(self, text: str) -> None:
        if self._eligible_token is None or self._transcript_overflow:
            return
        if len(self._transcript_parts) >= 12 or (
            sum(map(len, self._transcript_parts)) + len(text) > 300
        ):
            self._transcript_overflow = True
            self._transcript_parts.clear()
            return
        self._transcript_parts.append(text)

    def abort_turn(self) -> None:
        self._eligible_token = None
        self._transcript_parts.clear()
        self._transcript_overflow = False

    @staticmethod
    def _phrase(text: str) -> str:
        return " ".join(re.findall(r"[a-z]+", text.casefold()))

    def finish_turn(self) -> dict[str, str] | None:
        """Only exact affirmative/negative user speech in a subsequent turn counts."""
        token = self._eligible_token
        utterance = "" if self._transcript_overflow else " ".join(self._transcript_parts)
        self.abort_turn()
        draft = self.pending()
        if token is None or draft is None or token != draft.token:
            return None
        phrase = self._phrase(utterance)
        if phrase in APPROVAL_PHRASES:
            return self.approve()
        if phrase in REJECTION_PHRASES:
            return self.reject()
        return None

    def approve(self) -> dict[str, str]:
        draft = self.pending()
        if draft is None:
            return {"status": "error", "error": "no_pending_reminder"}
        try:
            item = self.store.reminder(draft.at, draft.text)
        except ValueError:
            self._pending = None
            return {"status": "error", "error": "time_no_longer_valid"}
        except Exception:
            # Keep the draft for an explicit retry; do not leak SQLite errors.
            return {"status": "error", "error": "save_failed"}
        self._pending = None
        self._eligible_token = None
        return {"status": "created", "id": item.id, "text": item.text, "at": draft.at}

    def reject(self) -> dict[str, str]:
        if self.pending() is None:
            return {"status": "error", "error": "no_pending_reminder"}
        self._pending = None
        self._eligible_token = None
        return {"status": "rejected"}


def register_reminder_draft(registry: ToolRegistry, approval: VoiceReminderApproval) -> None:
    registry.register(
        ToolSpec(
            name=DRAFT_TOOL_NAME,
            description=(
                "Draft one future one-time reminder for human review. This NEVER "
                "creates a reminder or writes to Google Calendar. First obtain "
                "current local time from get_local_time for relative dates, and "
                "use an explicit UTC offset. An app-controlled confirmation is required."
            ),
            arguments=DraftReminderArguments,
            handler=approval.propose,
            notice="Reminder proposal processed; no schedule was created.",
        )
    )
