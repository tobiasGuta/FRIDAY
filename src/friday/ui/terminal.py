"""Daemonized terminal input; a failed Live session cannot strand input() shutdown."""

from __future__ import annotations

import asyncio
import threading


class TerminalCommands:
    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Terminal reader already started")

        def reader() -> None:
            while not self._closed.is_set():
                try:
                    command = input("FRIDAY [Enter: talk/stop, /quit]: ")
                except (EOFError, KeyboardInterrupt):
                    command = "/quit"
                if self._closed.is_set():
                    return
                try:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, command)
                except RuntimeError:
                    return
                if command.strip().lower() in {"/quit", "/exit"}:
                    return

        self._thread = threading.Thread(target=reader, daemon=True, name="friday-terminal")
        self._thread.start()

    async def next(self) -> str:
        return await self._queue.get()

    def close(self) -> None:
        self._closed.set()
