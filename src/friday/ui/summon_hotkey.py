"""Optional Windows-only FRIDAY summon shortcut.

RegisterHotKey owns a dedicated thread message queue. Only the Qt signal may
request showing the existing desktop window; no mic, session or model action.
Nothing is registered until the user checks the Settings option.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

HOTKEY_ID = 0x4652
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
HOTKEY_MODIFIERS = MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
HOTKEY_VK = ord("F")
HOTKEY_LABEL = "Ctrl + Alt + Shift + F"
SUPPORTED = sys.platform == "win32"


def _windows_api() -> tuple[Any, Any]:
    """Load Win32 lazily so the desktop remains importable on Linux CI."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [
        wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
    ]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
        wintypes.UINT, wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
    ]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    return user32, kernel32


class SummonHotkeyBackend:
    """Win32 queue lifetime belongs to the worker thread, not the Qt window."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread_id = 0
        self._user32: Any = None

    def request_stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread_id = self._thread_id
            user32 = self._user32
        if thread_id and user32 is not None:
            # Must wake GetMessage, which otherwise blocks forever.
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)

    def run(
        self,
        on_trigger: Callable[[], None],
        on_registration: Callable[[bool, str], None],
        *,
        native_api: tuple[Any, Any] | None = None,
    ) -> None:
        if native_api is None:
            if not SUPPORTED:
                on_registration(False, "Global summon shortcut is Windows-only.")
                return
            try:
                native_api = _windows_api()
            except OSError:
                on_registration(False, "Windows hotkey service is unavailable.")
                return
        user32, kernel32 = native_api
        self._run_native(user32, kernel32, on_trigger, on_registration)

    def _run_native(
        self,
        user32: Any,
        kernel32: Any,
        on_trigger: Callable[[], None],
        on_registration: Callable[[bool, str], None],
    ) -> None:
        registered = False
        try:
            # Win32 requires a queue before another thread can post WM_QUIT.
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            with self._lock:
                self._user32 = user32
                self._thread_id = int(kernel32.GetCurrentThreadId())
            if self._stop.is_set():
                return
            registered = bool(user32.RegisterHotKey(
                None, HOTKEY_ID, HOTKEY_MODIFIERS, HOTKEY_VK,
            ))
            if not registered:
                on_registration(
                    False, "Shortcut unavailable; it may already be in use."
                )
                return
            on_registration(True, f"Summon shortcut active: {HOTKEY_LABEL}.")
            while not self._stop.is_set():
                result = int(user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
                if result <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    if not self._stop.is_set():
                        on_trigger()
        finally:
            if registered:
                # Unregister on the SAME worker thread that registered it.
                user32.UnregisterHotKey(None, HOTKEY_ID)
            with self._lock:
                self._thread_id = 0
                self._user32 = None


class SummonHotkeyThread(QThread):
    activated = Signal()
    registration = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self._backend = SummonHotkeyBackend()

    def request_stop(self) -> None:
        self._backend.request_stop()

    def run(self) -> None:
        try:
            self._backend.run(self.activated.emit, self.registration.emit)
        except Exception:
            # No native exception text or OS details are sent to the UI.
            self.registration.emit(False, "Summon shortcut stopped unexpectedly.")
