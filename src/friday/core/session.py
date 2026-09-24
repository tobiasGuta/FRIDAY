"""Own lifecycle, provider errors, and bounded event handoff."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import monotonic

from friday.core.events import EventKind, SessionState, VoiceEvent
from friday.core.provider import ProviderCapabilityError, VoiceProvider

logger = logging.getLogger(__name__)


class SessionError(RuntimeError):
    """An operation is invalid for the current session or its provider failed."""


@dataclass(frozen=True, slots=True)
class SessionMetrics:
    state: SessionState
    elapsed_seconds: float
    provider_events: int
    output_audio_bytes: int
    dropped_audio_events: int
    dropped_control_events: int


class SessionManager:
    """One session, one consuming UI. Never logs audio buffers or credentials."""

    def __init__(self, provider: VoiceProvider, *, queue_size: int = 128) -> None:
        if queue_size < 2:
            raise ValueError("queue_size must be at least 2")
        self._provider = provider
        self._queue: asyncio.Queue[VoiceEvent] = asyncio.Queue(maxsize=queue_size)
        self._pump_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self.state = SessionState.NEW
        self.dropped_audio_events = 0
        self.dropped_control_events = 0
        self._started_at: float | None = None
        self._provider_events = 0
        self._output_audio_bytes = 0

    def metrics(self) -> SessionMetrics:
        return SessionMetrics(
            state=self.state,
            elapsed_seconds=0.0 if self._started_at is None else monotonic() - self._started_at,
            provider_events=self._provider_events,
            output_audio_bytes=self._output_audio_bytes,
            dropped_audio_events=self.dropped_audio_events,
            dropped_control_events=self.dropped_control_events,
        )

    def _emit(self, event: VoiceEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Slow UI consumers cannot grow memory without bound. Prioritize lifecycle
            # signals over stale queued data; future audio I/O will use a separate queue.
            if event.kind is EventKind.AUDIO:
                self.dropped_audio_events += 1
                return
            removed = self._queue.get_nowait()
            if removed.kind is EventKind.AUDIO:
                self.dropped_audio_events += 1
            else:
                self.dropped_control_events += 1
            self._queue.put_nowait(event)

    def _set_state(self, state: SessionState) -> None:
        self.state = state
        logger.info("FRIDAY session state: %s", state.value)
        self._emit(VoiceEvent(EventKind.STATE, state=state))

    async def start(self) -> None:
        if self.state is not SessionState.NEW:
            raise SessionError(f"Cannot start session in state {self.state.value}")
        self._started_at = monotonic()
        self._set_state(SessionState.CONNECTING)
        try:
            await self._provider.connect()
        except Exception as exc:
            self._set_state(SessionState.FAILED)
            self._emit(
                VoiceEvent(
                    EventKind.ERROR, text=f"Provider connection failed ({type(exc).__name__})"
                )
            )
            raise SessionError("Provider connection failed") from exc
        self._set_state(SessionState.READY)
        self._pump_task = asyncio.create_task(self._pump(), name="friday-provider-events")

    async def _pump(self) -> None:
        try:
            async for event in self._provider.events():
                if self.state is not SessionState.READY:
                    break
                self._provider_events += 1
                if event.kind is EventKind.AUDIO:
                    self._output_audio_bytes += len(event.audio or b"")
                # A fast Live provider must not silently discard words when the
                # sound device is playing earlier audio. This bounded queue
                # propagates backpressure through the provider's receive loop.
                # Lifecycle/error signals still use _emit to remain deliverable.
                await self._queue.put(event)
            if self.state is SessionState.READY:
                self._set_state(SessionState.FAILED)
                self._emit(VoiceEvent(EventKind.ERROR, text="Provider event stream ended"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.state is SessionState.READY:
                self._set_state(SessionState.FAILED)
                self._emit(
                    VoiceEvent(
                        EventKind.ERROR, text=f"Provider event stream failed ({type(exc).__name__})"
                    )
                )

    def _require_ready(self) -> None:
        if self.state is not SessionState.READY:
            raise SessionError(f"Session is not ready (state={self.state.value})")

    async def _send(self, method: str, value: bytes | str | None = None) -> None:
        self._require_ready()
        try:
            action = getattr(self._provider, method)
            if value is None:
                await action()
            else:
                await action(value)
        except Exception as exc:
            # Capability errors are not provider connection failures.
            if isinstance(exc, ProviderCapabilityError):
                raise
            self._set_state(SessionState.FAILED)
            self._emit(
                VoiceEvent(
                    EventKind.ERROR, text=f"Provider operation failed ({type(exc).__name__})"
                )
            )
            raise SessionError(f"Provider operation failed: {method}") from exc

    async def send_text(self, text: str) -> None:
        if not text.strip():
            raise ValueError("Text message cannot be empty")
        await self._send("send_text", text)

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm or len(pcm) % 2:
            raise ValueError("PCM must contain complete 16-bit samples")
        await self._send("send_audio", pcm)

    async def end_input(self) -> None:
        await self._send("end_input")

    async def start_activity(self) -> None:
        await self._send("start_activity")

    async def end_activity(self) -> None:
        await self._send("end_activity")

    async def events(self) -> AsyncIterator[VoiceEvent]:
        """Single-consumer event stream; ends after CLOSED is received."""
        while True:
            event = await self._queue.get()
            yield event
            if event.kind is EventKind.STATE and event.state is SessionState.CLOSED:
                return

    async def close(self) -> None:
        async with self._close_lock:
            if self.state is SessionState.CLOSED:
                return
            if self._pump_task is not None:
                self._pump_task.cancel()
                try:
                    await self._pump_task
                except asyncio.CancelledError:
                    pass
            try:
                await self._provider.close()
            finally:
                self._set_state(SessionState.CLOSED)
