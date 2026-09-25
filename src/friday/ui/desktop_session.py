"""Desktop voice orchestration without any Qt or provider SDK dependency.

The GUI sends bounded commands to this event loop; this class is the only
consumer of SessionManager.events(). Qt widgets never touch PortAudio or Gemini.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from friday.audio.devices import AudioDeviceError
from friday.audio.turns import VoiceTurns
from friday.core.events import EventKind, SessionState
from friday.core.session import SessionError, SessionManager
from friday.voice_reminders import ReminderDraft, VoiceReminderApproval

UiEvent = Callable[[str, Any], None]
VOICE_STALL_SECONDS = 30.0


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
        if not 5 <= max_seconds <= 3600:
            raise ValueError("Session duration must be 5 to 3600 seconds")
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
        self.played_at_turn_start = 0
        self.completions = 0
        self.generation_completions = 0
        self.assistant_chunks = 0
        self._end_reason: str | None = None
        self._quit_requested = False

    def request(self, command: str) -> None:
        """Must be called on the owning asyncio loop, not the Qt thread."""
        if command not in {"start", "stop", "approve", "reject", "quit"}:
            return
        if command == "quit":
            self._quit_requested = True
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
                    if event.speaker == "assistant":
                        self.assistant_chunks += 1
                    self.emit("transcript", {"speaker": event.speaker, "text": event.text})
            elif event.kind is EventKind.GENERATION_COMPLETE:
                # Generation completion does NOT unlock the microphone; only
                # the server's final TURN_COMPLETE or INTERRUPTED signal can.
                self.generation_completions += 1
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
                self._end_reason = "connection"
                self.emit("error", event.text or "Voice provider error")
                return False
            elif event.kind is EventKind.STATE and event.state is SessionState.CLOSED:
                return True
        return False

    async def run(self) -> None:
        """One explicit Live connection. Recovery never opens another paid session."""
        receiver: asyncio.Task[bool] | None = None
        self._end_reason = None
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
            started = loop.time()
            expires = started + self.max_seconds
            warning = expires - min(30, max(1, self.max_seconds // 5))
            warned = False
            deadline: float | None = None
            progress: tuple[int, int, int, int, int] | None = None
            # An already-started spoken reply gets at most 30 seconds to finish
            # after the normal limit; a new turn is never admitted after expiry.
            async with asyncio.timeout(self.max_seconds + 30):
                while True:
                    now = loop.time()
                    if self._quit_requested:
                        break
                    if not warned and now >= warning:
                        warned = True
                        self.emit(
                            "notice",
                            "Voice session nearing its time limit. "
                            "Finish this turn; reconnect explicitly to continue.",
                        )
                    if now >= expires:
                        if self.turns.recording:
                            self.emit(
                                "notice",
                                "Session time limit reached while recording; "
                                "microphone capture is being stopped.",
                            )
                        if not self.turns.awaiting_response:
                            self._end_reason = "expired"
                            self.emit("notice", "Voice session ended at its time limit.")
                            break

                    # Detect a microphone unplug or a failed audio sender even if
                    # the user never clicks Stop. Poll while recording; the Qt UI
                    # remains responsive and no audio data reaches the UI.
                    self.turns.check_microphone()
                    sender = self.turns.sender_task if self.turns.recording else None
                    command_task = asyncio.create_task(self.commands.get())
                    finished_task = (
                        asyncio.create_task(self.turn_finished.wait())
                        if self.turns.awaiting_response else None
                    )
                    waiters: set[asyncio.Task[Any]] = {command_task, receiver}
                    if finished_task is not None:
                        waiters.add(finished_task)
                    if sender is not None:
                        waiters.add(sender)
                    tick = (
                        0.2 if self.turns.recording or self.turns.awaiting_response
                        else min(5.0, max(0.0, (expires if warned else warning) - now))
                    )
                    try:
                        done, _ = await asyncio.wait(
                            waiters, timeout=tick, return_when=asyncio.FIRST_COMPLETED
                        )
                        if receiver in done:
                            if not self._quit_requested:
                                self._end_reason = "connection"
                                if not receiver.result():
                                    self.emit("status", "Connection failed")
                                else:
                                    self.emit(
                                        "notice",
                                        "Voice connection ended unexpectedly. "
                                        "Reconnect explicitly to continue.",
                                    )
                            break
                        if command_task in done and command_task.result() == "quit":
                            break
                        if finished_task is not None and finished_task in done:
                            if not await self.speaker.wait_until_drained(timeout=5.0):
                                self.speaker.flush()
                                self.emit("notice", "Speaker buffer did not finish playing.")
                            self.turns.response_finished()
                            self.turn_finished.clear()
                            deadline = None
                            if loop.time() >= expires:
                                self._end_reason = "expired"
                                self.emit("notice", "Voice session ended at its time limit.")
                                break
                            self.emit("status", "Ready")
                            self._pending()
                        if command_task in done:
                            command = command_task.result()
                            if command == "start" and not self.turns.recording and (
                                not self.turns.awaiting_response
                            ) and loop.time() < expires:
                                self.turn_finished.clear()
                                self.user_chunks = 0
                                self.audio_bytes = 0
                                self.played_at_turn_start = self.speaker.played_bytes
                                self.completions = 0
                                self.generation_completions = 0
                                self.assistant_chunks = 0
                                if self.approval is not None:
                                    self.approval.begin_turn()
                                await self.turns.start()
                                self.emit("status", "Listening")
                            elif command == "stop" and self.turns.recording:
                                await self.turns.stop()
                                self.emit("status", "Responding")
                                deadline = loop.time() + VOICE_STALL_SECONDS
                                progress = None
                            elif command in {"approve", "reject"} and (
                                self.approval is not None and not self.turns.recording
                                and not self.turns.awaiting_response
                            ) and loop.time() < expires:
                                self._result(
                                    self.approval.approve() if command == "approve"
                                    else self.approval.reject()
                                )
                        if sender is not None and sender in done and self.turns.recording:
                            if sender.cancelled():
                                raise AudioDeviceError("Microphone capture stopped unexpectedly")
                            # Raises the original SessionError if sending failed.
                            sender.result()
                            raise AudioDeviceError("Microphone capture ended unexpectedly")
                        if self.turns.awaiting_response and deadline is not None:
                            current = (
                                self.user_chunks, self.audio_bytes,
                                self.completions, self.speaker.played_bytes,
                                self.generation_completions,
                            )
                            if current != progress:
                                progress = current
                                deadline = loop.time() + VOICE_STALL_SECONDS
                            elif loop.time() >= deadline:
                                self._end_reason = "connection"
                                # These fixed labels/counters do not expose
                                # PCM, transcript, provider payloads or keys.
                                self.emit(
                                    "notice",
                                    "Voice turn-end diagnostics: "
                                    f"generation_complete={bool(self.generation_completions)}; "
                                    f"turn_complete={bool(self.completions)}; "
                                    f"assistant_text={bool(self.assistant_chunks)}; "
                                    f"audio_received={bool(self.audio_bytes)}; "
                                    f"audio_callback={self.speaker.played_bytes > self.played_at_turn_start}.",
                                )
                                self.emit(
                                    "error",
                                    "Voice response stalled for 30 seconds. "
                                    "Final turn completion was not received; "
                                    "reconnect manually.",
                                )
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
            if not self._quit_requested:
                self._end_reason = "expired"
                self.emit("notice", "Voice session time limit reached; reconnect to continue.")
        except AudioDeviceError:
            if not self._quit_requested:
                self._end_reason = "audio"
                self.emit(
                    "error",
                    "Audio device unavailable. Check microphone/speaker connection "
                    "and Windows permissions, then reconnect.",
                )
        except SessionError:
            if not self._quit_requested:
                self._end_reason = "connection"
                self.emit("error", "Voice connection interrupted. Reconnect to continue.")
        finally:
            self.emit("status", "Disconnecting")
            try:
                await self.turns.close()
            except AudioDeviceError:
                if not self._quit_requested:
                    self._end_reason = self._end_reason or "audio"
                    self.emit("notice", "Audio device reported a shutdown problem.")
            try:
                if receiver is not None:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                await self.manager.close()
            finally:
                try:
                    self.speaker.close()
                except AudioDeviceError:
                    if not self._quit_requested:
                        self._end_reason = self._end_reason or "audio"
                        self.emit("notice", "Speaker could not close normally.")
                finally:
                    # Never carry a pending model-proposed action into a fresh session.
                    # Discard the host-owned draft itself, not just the visible panel.
                    if self.approval is not None:
                        self.approval.abort_turn()
                        self.approval.reject()
                    self.emit("draft", None)
                    if self._end_reason is not None and not self._quit_requested:
                        self.emit("recovery", self._end_reason)
                    self.emit("status", "Disconnected")
