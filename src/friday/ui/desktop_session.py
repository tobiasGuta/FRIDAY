"""Desktop voice orchestration without any Qt or provider SDK dependency.

The GUI sends bounded commands to this event loop; this class is the only
consumer of SessionManager.events(). Qt widgets never touch PortAudio or Gemini.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from friday.audio.turns import VoiceTurns
from friday.core.events import EventKind, SessionState
from friday.core.session import SessionManager
from friday.voice_reminders import ReminderDraft, VoiceReminderApproval

UiEvent = Callable[[str, Any], None]


def draft_display(draft: ReminderDraft | None) -> dict[str, str] | None:
    """Produce only the pending app-owned draft; never trust model confirmation."""
    if draft is None:
        return None
    result = {"action": draft.action, "text": draft.text, "at": draft.at}
    if draft.original is not None:
        result["id"] = draft.original.id
        result["before_text"] = draft.original.text
        result["before_at"] = VoiceReminderApproval._local_at(draft.original)
    return result


class DesktopVoiceSession:
    """Single-session event loop. Command requests are serialized here."""

    def __init__(
        self,
        manager: SessionManager,
        microphone: Any,
        speaker: Any,
        *,
        approval: VoiceReminderApproval | None,
        emit: UiEvent,
        max_seconds: int,
    ) -> None:
        if max_seconds < 5:
            raise ValueError("Session duration must be at least 5 seconds")
        self.manager = manager
        self.speaker = speaker
        self.turns = VoiceTurns(manager, microphone, speaker)
        self.approval = approval
        self.emit = emit
        self.max_seconds = max_seconds
        self.commands: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self.turn_finished = asyncio.Event()
        self.user_chunks = 0
        self.audio_bytes = 0
        self.completions = 0

    def request(self, command: str) -> None:
        """Must be called on the owning asyncio loop, not the Qt thread."""
        if command not in {"start", "stop", "approve", "reject", "quit"}:
            return
        if self.commands.full():
            if command != "quit":
                return
            self.commands.get_nowait()
        self.commands.put_nowait(command)

    def _pending(self) -> None:
        if self.approval is not None:
            self.emit("draft", draft_display(self.approval.pending()))

    def _reminders(self) -> None:
        if self.approval is None:
            self.emit("reminders", [])
            return
        items = self.approval.store.list_pending_reminders(limit=10)
        self.emit(
            "reminders",
            [
                {"text": item.text, "at": self.approval._local_at(item), "id": item.id}
                for item in items
            ],
        )

    def _result(self, result: dict[str, str] | None) -> None:
        if result is not None:
            self.emit("result", result)
            self._reminders()
        self._pending()

    async def _consume(self) -> bool:
        async for event in self.manager.events():
            if event.kind is EventKind.AUDIO:
                await self.speaker.enqueue_wait(
                    event.audio or b"", sample_rate=event.sample_rate or 0
                )
                self.audio_bytes += len(event.audio or b"")
            elif event.kind is EventKind.TRANSCRIPT:
                if event.speaker == "user":
                    self.user_chunks += 1
                    if self.approval is not None and event.text:
                        self.approval.hear_user(event.text)
                if event.text:
                    self.emit("transcript", {"speaker": event.speaker, "text": event.text})
            elif event.kind is EventKind.NOTICE:
                self.emit("notice", event.text or "")
                self._pending()
            elif event.kind is EventKind.GROUNDING:
                self.emit(
                    "sources",
                    [{"title": item.title, "url": item.url} for item in event.sources][:10],
                )
            elif event.kind is EventKind.INTERRUPTED:
                if self.approval is not None:
                    self.approval.abort_turn()
                self.speaker.flush()
                self.turn_finished.set()
            elif event.kind is EventKind.TURN_COMPLETE:
                self.completions += 1
                if self.approval is not None:
                    self._result(self.approval.finish_turn())
                self.turn_finished.set()
            elif event.kind is EventKind.ERROR:
                self.emit("error", event.text or "Voice provider error")
                return False
            elif event.kind is EventKind.STATE and event.state is SessionState.CLOSED:
                return True
        return False

    async def run(self) -> None:
        receiver: asyncio.Task[bool] | None = None
        try:
            self.emit("status", "Connecting")
            # Do not make a paid connection if audio hardware cannot initialize.
            self.speaker.start()
            await self.manager.start()
            receiver = asyncio.create_task(self._consume(), name="friday-desktop-events")
            self._reminders()
            self._pending()
            self.emit("status", "Ready")
            loop = asyncio.get_running_loop()
            deadline: float | None = None
            progress: tuple[int, int, int, int] | None = None
            async with asyncio.timeout(self.max_seconds):
                while True:
                    command_task = asyncio.create_task(self.commands.get())
                    finished_task = (
                        asyncio.create_task(self.turn_finished.wait())
                        if self.turns.awaiting_response else None
                    )
                    waiters = {command_task, receiver}
                    if finished_task is not None:
                        waiters.add(finished_task)
                    try:
                        done, _ = await asyncio.wait(
                            waiters,
                            timeout=0.2 if self.turns.awaiting_response else None,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if receiver in done:
                            if not receiver.result():
                                self.emit("status", "Connection failed")
                            break
                        if finished_task is not None and finished_task in done:
                            if not await self.speaker.wait_until_drained(timeout=5.0):
                                self.speaker.flush()
                                self.emit("notice", "Speaker buffer did not finish playing.")
                            self.turns.response_finished()
                            self.turn_finished.clear()
                            deadline = None
                            self.emit("status", "Ready")
                            self._pending()
                        if command_task in done:
                            command = command_task.result()
                            if command == "quit":
                                break
                            if command == "start" and not self.turns.recording and (
                                not self.turns.awaiting_response
                            ):
                                self.turn_finished.clear()
                                self.user_chunks = 0
                                self.audio_bytes = 0
                                self.completions = 0
                                if self.approval is not None:
                                    self.approval.begin_turn()
                                await self.turns.start()
                                self.emit("status", "Listening")
                            elif command == "stop" and self.turns.recording:
                                await self.turns.stop()
                                self.emit("status", "Responding")
                                deadline = loop.time() + 30.0
                                progress = None
                            elif command in {"approve", "reject"} and (
                                self.approval is not None and not self.turns.recording
                                and not self.turns.awaiting_response
                            ):
                                self._result(
                                    self.approval.approve() if command == "approve"
                                    else self.approval.reject()
                                )
                        if self.turns.awaiting_response and deadline is not None:
                            current = (
                                self.user_chunks, self.audio_bytes,
                                self.completions, self.speaker.played_bytes,
                            )
                            if current != progress:
                                progress = current
                                deadline = loop.time() + 30.0
                            elif loop.time() >= deadline:
                                self.emit("error", "Voice response stalled for 30 seconds.")
                                break
                    finally:
                        pending_tasks = [command_task]
                        if finished_task is not None:
                            pending_tasks.append(finished_task)
                        for task in pending_tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*pending_tasks, return_exceptions=True)
        except TimeoutError:
            self.emit("notice", "Session time limit reached. Reconnect to continue.")
        finally:
            self.emit("status", "Disconnecting")
            try:
                await self.turns.close()
            finally:
                if receiver is not None:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                try:
                    await self.manager.close()
                finally:
                    self.speaker.close()
            self.emit("draft", None)
            self.emit("status", "Disconnected")
