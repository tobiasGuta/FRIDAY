"""Bounded, 16-bit mono local audio I/O through an optional sounddevice backend.

PortAudio invokes callbacks on its own thread. Neither callback touches the model,
network, console, or asyncio queues directly. Input crosses to the event loop via
call_soon_threadsafe; output is filled from a bounded byte buffer under a short lock.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class AudioDeviceError(RuntimeError):
    """A microphone or speaker could not be opened or used."""


def _backend() -> Any:
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        raise AudioDeviceError(
            "Audio support is unavailable; run: py -m pip install -e '.[gemini,voice,dev]'"
        ) from exc
    return sd


def list_audio_devices() -> str:
    """Human-readable PortAudio device inventory; never touches the API key."""
    try:
        return str(_backend().query_devices())
    except Exception as exc:
        raise AudioDeviceError("Unable to enumerate audio devices") from exc


class Microphone:
    """40 ms, 16 kHz mono int16 frames; bounded producer-to-consumer queue."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        sample_rate: int = 16000,
        blocksize: int = 640,
        queue_size: int = 12,
        device: int | None = None,
        backend: Any | None = None,
    ) -> None:
        if sample_rate != 16000 or blocksize <= 0 or queue_size < 2:
            raise ValueError("Invalid microphone PCM settings")
        self._loop = loop
        self._backend = backend
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._device = device
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_size)
        self._stream: Any = None
        self._recording = False
        self.dropped_chunks = 0
        self.status_events = 0

    @property
    def recording(self) -> bool:
        return self._recording

    def _offer(self, pcm: bytes) -> None:
        if not self._recording:
            return
        if self._queue.full():
            self._queue.get_nowait()  # Old audio is less useful than the current frame.
            self.dropped_chunks += 1
        self._queue.put_nowait(pcm)

    def _callback(self, indata: Any, frames: int, timing: Any, status: Any) -> None:
        del frames, timing
        try:
            if status:
                self._loop.call_soon_threadsafe(self._mark_status)
            if self._recording:
                self._loop.call_soon_threadsafe(self._offer, bytes(indata))
        except RuntimeError:
            # The event loop is closing; do not keep capturing after teardown.
            pass

    def _mark_status(self) -> None:
        self.status_events += 1

    def start(self) -> None:
        if self._recording:
            raise AudioDeviceError("Microphone is already recording")
        if self._stream is not None:
            raise AudioDeviceError("Microphone must be stopped before restarting")
        # Remove any stale chunks/sentinel from the previous recording turn.
        while not self._queue.empty():
            self._queue.get_nowait()
        sd = self._backend if self._backend is not None else _backend()
        try:
            stream = sd.RawInputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self._blocksize,
                device=self._device,
                callback=self._callback,
            )
            self._recording = True
            stream.start()
        except Exception as exc:
            self._recording = False
            if "stream" in locals():
                stream.close()
            raise AudioDeviceError(
                "Unable to start the microphone; check device and permissions"
            ) from exc
        self._stream = stream

    def stop(self) -> None:
        """Stop capture and deliver every already queued frame before the sentinel."""
        self._recording = False
        stream, self._stream = self._stream, None
        device_error = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                device_error = exc
            finally:
                try:
                    stream.close()
                except Exception as exc:
                    device_error = device_error or exc
        # Reserve one queue position for EOF; the oldest frame may be discarded.
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped_chunks += 1
        self._queue.put_nowait(None)
        if device_error is not None:
            raise AudioDeviceError("Microphone failed while stopping") from device_error

    async def chunks(self):
        while True:
            frame = await self._queue.get()
            if frame is None:
                return
            yield frame


class Speaker:
    """24 kHz PCM speaker with a finite buffer and immediate interruption flush."""

    def __init__(
        self,
        *,
        sample_rate: int = 24000,
        blocksize: int = 960,
        max_buffer_seconds: int = 4,
        device: int | None = None,
        backend: Any | None = None,
    ) -> None:
        if sample_rate != 24000 or blocksize <= 0 or max_buffer_seconds < 1:
            raise ValueError("Invalid speaker PCM settings")
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._max_bytes = sample_rate * 2 * max_buffer_seconds
        self._device = device
        self._backend = backend
        self._pending = bytearray()
        self._lock = threading.Lock()
        self._stream: Any = None
        self.dropped_bytes = 0
        self.status_events = 0

    def _callback(self, outdata: Any, frames: int, timing: Any, status: Any) -> None:
        del frames, timing
        if status:
            self.status_events += 1
        with self._lock:
            length = min(len(outdata), len(self._pending))
            data = bytes(self._pending[:length])
            del self._pending[:length]
        outdata[:] = data + bytes(len(outdata) - length)

    def start(self) -> None:
        if self._stream is not None:
            raise AudioDeviceError("Speaker is already open")
        sd = self._backend if self._backend is not None else _backend()
        try:
            stream = sd.RawOutputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self._blocksize,
                device=self._device,
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:
            if "stream" in locals():
                stream.close()
            raise AudioDeviceError("Unable to start speaker; check output device settings") from exc
        self._stream = stream

    def enqueue(self, pcm: bytes, *, sample_rate: int) -> None:
        if sample_rate != self._sample_rate or len(pcm) % 2:
            raise AudioDeviceError("Speaker requires 24 kHz mono int16 PCM")
        if not pcm:
            return
        with self._lock:
            combined = self._pending + pcm
            if len(combined) > self._max_bytes:
                excess = len(combined) - self._max_bytes
                # Sample aligned, so we never split a 16-bit sample.
                excess += excess % 2
                self.dropped_bytes += excess
                del combined[:excess]
            self._pending = combined

    def flush(self) -> None:
        with self._lock:
            self._pending.clear()

    def close(self) -> None:
        self.flush()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
