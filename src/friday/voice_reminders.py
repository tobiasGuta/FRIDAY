"""App-owned voice reminder proposals and explicit human approval.

The model can read a bounded reminder list and draft changes, but it cannot
authorize or directly mutate SQLite. Approval applies only to the exact version
reviewed, and only in a separate user turn or through a terminal command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import Field

from friday.schedule import MAX_TEXT, Schedule, ScheduleStore, parse_due
from friday.tools.registry import (
    NoArguments,
    ToolArguments,
    ToolRegistry,
    ToolSpec,
)

DRAFT_TOOL_NAME = "draft_reminder"
LIST_TOOL_NAME = "get_reminders"
EDIT_TOOL_NAME = "draft_edit_reminder"
CANCEL_TOOL_NAME = "draft_cancel_reminder"
APPROVAL_PHRASES = frozenset({
    "yes", "yes please", "yes create it", "yes create that reminder",
    "yes create the reminder", "yes move it", "yes update it", "yes cancel it",
    "yes delete it", "approve reminder", "approve the reminder",
    "confirm reminder", "confirm the reminder",
})
REJECTION_PHRASES = frozenset({
    "no", "no thanks", "no thank you", "cancel reminder",
    "cancel the reminder", "reject reminder", "reject the reminder",
})


class DraftReminderArguments(ToolArguments):
    text: str = Field(
        min_length=1, max_length=MAX_TEXT,
        description="Brief reminder text without control characters (maximum 200 characters).",
    )
    at: str = Field(
        min_length=20, max_length=40,
        description=(
            "Future ISO 8601 date and time with an explicit UTC offset, e.g. "
            "2026-09-27T19:00:00-04:00. Call get_local_time first for relative dates."
        ),
    )


class ReminderIdArguments(ToolArguments):
    id: str = Field(
        min_length=32, max_length=32,
        description="Exact 32-character reminder ID returned by get_reminders.",
    )


class EditReminderArguments(ReminderIdArguments):
    text: str = Field(
        min_length=1, max_length=MAX_TEXT,
        description="Complete new reminder text, even when unchanged.",
    )
    at: str = Field(
        min_length=20, max_length=40,
        description=(
            "Complete new future ISO 8601 due time including UTC offset, even if unchanged. "
            "Call get_local_time before interpreting relative dates."
        ),
    )


@dataclass(frozen=True, slots=True)
class ReminderDraft:
    token: str
    text: str
    at: str
    created_at: float
    action: str = "create"
    original: Schedule | None = None


class VoiceReminderApproval:
    """One ephemeral proposal at a time; only this host can write schedules."""

    def __init__(self, store: ScheduleStore, *, ttl_seconds: float = 300.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Draft TTL must be positive")
        self.store = store
        self.ttl_seconds = ttl_seconds
        self._pending: ReminderDraft | None = None
        self._listed: dict[str, Schedule] = {}
        self._eligible_token: str | None = None
        self._transcript_parts: list[str] = []
        self._transcript_overflow = False

    @staticmethod
    def _local_at(item: Schedule) -> str:
        return datetime.fromtimestamp(item.due_at).astimezone().isoformat(timespec="seconds")

    def pending(self) -> ReminderDraft | None:
        draft = self._pending
        if draft is not None and monotonic() - draft.created_at >= self.ttl_seconds:
            self._pending = None
            self._eligible_token = None
            return None
        return draft

    def list_reminders(self, _arguments: NoArguments) -> dict[str, Any]:
        candidates = self.store.list_pending_reminders(limit=26)
        items = candidates[:25]
        self._listed = {item.id: item for item in items}
        return {
            "status": "ok",
            "reminders": [
                {"id": item.id, "text": item.text, "at": self._local_at(item)}
                for item in items
            ],
            "limit": 25,
            "truncated": len(candidates) > 25,
            "instruction": (
                "Use an exact ID from this list for edit/cancel proposals. "
                "If several reminders match, ask the user to clarify the date and time."
            ),
        }

    @staticmethod
    def _validated_text(text: str) -> str | None:
        value = text.strip()
        if not 1 <= len(value) <= MAX_TEXT:
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in text):
            return None
        return value

    def propose(self, arguments: DraftReminderArguments) -> dict[str, str]:
        if self.pending() is not None:
            return {"status": "error", "error": "pending_approval"}
        text = self._validated_text(arguments.text)
        if text is None:
            return {"status": "error", "error": "invalid_text"}
        try:
            parse_due(arguments.at)
        except (TypeError, ValueError):
            return {"status": "error", "error": "invalid_due_time"}
        draft = ReminderDraft(uuid4().hex, text, arguments.at, monotonic())
        self._pending = draft
        return {
            "status": "ok", "state": "pending_approval", "action": draft.action,
            "text": draft.text, "at": draft.at,
            "instruction": (
                "Only a draft. Ask the user to approve it in a separate voice turn. "
                "Only the application can save it."
            ),
        }

    def _candidate(self, reminder_id: str) -> Schedule | None:
        listed = self._listed.get(reminder_id)
        if listed is None:
            return None
        latest = self.store.get_pending_reminder(reminder_id)
        if latest is None or latest.revision != listed.revision:
            return None
        if latest.text != listed.text or latest.due_at != listed.due_at:
            return None
        return listed

    def propose_edit(self, arguments: EditReminderArguments) -> dict[str, str]:
        if self.pending() is not None:
            return {"status": "error", "error": "pending_approval"}
        item = self._candidate(arguments.id)
        if item is None:
            return {"status": "error", "error": "not_listed_or_stale"}
        text = self._validated_text(arguments.text)
        if text is None:
            return {"status": "error", "error": "invalid_text"}
        try:
            due = parse_due(arguments.at)
        except (TypeError, ValueError):
            return {"status": "error", "error": "invalid_due_time"}
        if item.text == text and item.due_at == due.timestamp():
            return {"status": "error", "error": "no_change"}
        draft = ReminderDraft(
            uuid4().hex, text, arguments.at, monotonic(), "edit", item
        )
        self._pending = draft
        return {
            "status": "ok", "state": "pending_approval", "action": "edit",
            "id": item.id, "previous_text": item.text,
            "previous_at": self._local_at(item), "text": text, "at": arguments.at,
            "instruction": "Ask the user to approve this precise change in a new voice turn.",
        }

    def propose_cancel(self, arguments: ReminderIdArguments) -> dict[str, str]:
        if self.pending() is not None:
            return {"status": "error", "error": "pending_approval"}
        item = self._candidate(arguments.id)
        if item is None:
            return {"status": "error", "error": "not_listed_or_stale"}
        draft = ReminderDraft(
            uuid4().hex, item.text, self._local_at(item), monotonic(),
            "cancel", item,
        )
        self._pending = draft
        return {
            "status": "ok", "state": "pending_approval", "action": "cancel",
            "id": item.id, "text": item.text, "at": draft.at,
            "instruction": "Ask the user to approve cancellation in a new voice turn.",
        }

    def begin_turn(self) -> None:
        """Freeze eligibility before recording; cannot propose and approve in one turn."""
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
            if draft.action == "create":
                item = self.store.reminder(draft.at, draft.text)
                result = {
                    "status": "created", "id": item.id,
                    "text": item.text, "at": draft.at,
                }
            elif draft.original is not None and draft.action == "edit":
                item = self.store.edit_reminder(
                    draft.original, text=draft.text, when=draft.at
                )
                if item is None:
                    self._pending = None
                    return {"status": "error", "error": "stale_reminder"}
                result = {
                    "status": "updated", "id": item.id,
                    "text": item.text, "at": self._local_at(item),
                }
            elif draft.original is not None and draft.action == "cancel":
                if not self.store.cancel_reminder_if_unchanged(draft.original):
                    self._pending = None
                    return {"status": "error", "error": "stale_reminder"}
                result = {
                    "status": "cancelled", "id": draft.original.id,
                    "text": draft.text, "at": draft.at,
                }
            else:
                self._pending = None
                return {"status": "error", "error": "invalid_draft"}
        except ValueError:
            self._pending = None
            return {"status": "error", "error": "time_or_text_no_longer_valid"}
        except Exception:
            # Preserve the draft for explicit retry; don't expose database details.
            return {"status": "error", "error": "save_failed"}
        self._pending = None
        self._eligible_token = None
        self._listed.clear()
        return result

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
                "Draft a new future one-time reminder; NEVER creates or saves it. "
                "Use get_local_time for relative dates and an explicit UTC offset."
            ),
            arguments=DraftReminderArguments,
            handler=approval.propose,
            notice="Reminder proposal processed; nothing saved yet.",
        )
    )


def register_reminder_management(
    registry: ToolRegistry, approval: VoiceReminderApproval
) -> None:
    """Read-only listing and mutation-free proposals; actual writes stay host-only."""
    registry.register(
        ToolSpec(
            name=LIST_TOOL_NAME,
            description=(
                "List current pending future reminders, IDs, text, and local times. "
                "Call this before identifying any reminder to edit or cancel. "
                "If names repeat, ask the user which date/time they mean."
            ),
            arguments=NoArguments,
            handler=approval.list_reminders,
            notice="Read pending reminders from local SQLite.",
        )
    )
    registry.register(
        ToolSpec(
            name=EDIT_TOOL_NAME,
            description=(
                "Propose editing ONE exact reminder ID from get_reminders, using the "
                "complete new text and full future ISO time with UTC offset. Does NOT "
                "save. Ask the user to confirm the old and new details separately."
            ),
            arguments=EditReminderArguments,
            handler=approval.propose_edit,
            notice="Proposed reminder edit; no changes saved.",
        )
    )
    registry.register(
        ToolSpec(
            name=CANCEL_TOOL_NAME,
            description=(
                "Propose cancellation of ONE exact reminder ID from get_reminders. "
                "This does NOT cancel it. Ask the user to confirm separately."
            ),
            arguments=ReminderIdArguments,
            handler=approval.propose_cancel,
            notice="Proposed reminder cancellation; nothing cancelled yet.",
        )
    )
