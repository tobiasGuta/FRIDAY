"""Offline summon shortcut tests: no real global registration or voice session."""

import ctypes
import os
from ctypes import wintypes

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from friday.ui import desktop
from friday.ui.desktop import DesktopWindow
from friday.ui.summon_hotkey import (
    HOTKEY_ID,
    HOTKEY_LABEL,
    HOTKEY_MODIFIERS,
    HOTKEY_VK,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    WM_HOTKEY,
    WM_QUIT,
    SummonHotkeyBackend,
)


class FakeKernel32:
    def GetCurrentThreadId(self):
        return 1234


class FakeUser32:
    def __init__(self, *, register=True):
        self.register = register
        self.registrations = []
        self.unregisters = []
        self.posts = []
        self.reads = 0
        self.queued = []

    def PeekMessageW(self, *args):
        return 0

    def RegisterHotKey(self, *args):
        self.registrations.append(args)
        return self.register

    def UnregisterHotKey(self, *args):
        self.unregisters.append(args)
        return True

    def PostThreadMessageW(self, *args):
        self.posts.append(args)
        return True

    def GetMessageW(self, ptr, *args):
        self.reads += 1
        if not self.queued:
            return 0
        message, ident = self.queued.pop(0)
        msg = ctypes.cast(ptr, ctypes.POINTER(wintypes.MSG)).contents
        msg.message = message
        msg.wParam = ident
        return 1


def test_native_hotkey_registers_one_nonrepeating_non_win_combo_and_unregisters():
    backend = SummonHotkeyBackend()
    user32 = FakeUser32()
    user32.queued = [(WM_HOTKEY, HOTKEY_ID + 1), (WM_HOTKEY, HOTKEY_ID)]
    states = []
    activated = []

    def hit():
        activated.append(True)
        backend.request_stop()

    backend.run(hit, lambda ok, label: states.append((ok, label)),
                native_api=(user32, FakeKernel32()))
    assert user32.registrations == [
        (None, HOTKEY_ID, MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, HOTKEY_VK)
    ]
    assert HOTKEY_MODIFIERS & 0x0008 == 0  # Never register reserved Win+ combos.
    assert states == [(True, f"Summon shortcut active: {HOTKEY_LABEL}.")]
    assert activated == [True]
    assert user32.posts == [(1234, WM_QUIT, 0, 0)]
    assert user32.unregisters == [(None, HOTKEY_ID)]


def test_conflicting_shortcut_is_nonfatal_and_is_not_unregistered():
    backend = SummonHotkeyBackend()
    user32 = FakeUser32(register=False)
    messages = []
    backend.run(lambda: None, lambda *args: messages.append(args),
                native_api=(user32, FakeKernel32()))
    assert messages == [(False, "Shortcut unavailable; it may already be in use.")]
    assert user32.unregisters == []
    assert user32.reads == 0


def test_disable_before_registration_never_takes_global_shortcut():
    backend = SummonHotkeyBackend()
    backend.request_stop()
    user32 = FakeUser32()
    backend.run(lambda: None, lambda *args: None,
                native_api=(user32, FakeKernel32()))
    assert user32.registrations == []
    assert user32.unregisters == []


class FakeHotkey(QObject):
    activated = Signal()
    registration = Signal(bool, str)
    finished = Signal()

    instances = []

    def __init__(self):
        super().__init__()
        self.stops = 0
        self.instances.append(self)

    def start(self):
        self.registration.emit(True, "Summon shortcut active.")

    def request_stop(self):
        self.stops += 1
        self.finished.emit()


def test_hotkey_is_opt_in_summons_hidden_window_without_starting_voice(
    monkeypatch, tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(desktop, "SUMMON_SUPPORTED", True)
    monkeypatch.setattr(desktop, "SummonHotkeyThread", FakeHotkey)
    FakeHotkey.instances.clear()
    window = DesktopWindow()
    try:
        assert app is not None
        assert not window.summon_hotkey_option.isChecked()
        assert window._hotkey_thread is None
        assert FakeHotkey.instances == []
        assert window._worker is None
        assert not window.mic_button.isEnabled()
        window._navigate("Settings")
        window.summon_hotkey_option.setChecked(True)
        app.processEvents()
        fake = FakeHotkey.instances[-1]
        assert window._hotkey_thread is fake
        assert "active" in window.summon_hotkey_notice.text()
        window.hide()
        assert not window.isVisible()
        fake.activated.emit()
        app.processEvents()
        assert window.isVisible()
        assert window._worker is None
        assert window._state == "Disconnected"
        assert not window.mic_button.isEnabled()
        window.summon_hotkey_option.setChecked(False)
        assert fake.stops == 1
        assert window._hotkey_thread is None
        assert not window.summon_hotkey_option.isChecked()
    finally:
        window._tray = None
        window.close()


def test_unavailable_hotkey_disables_option_without_launch_or_retry(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(desktop, "SUMMON_SUPPORTED", True)

    class FailedHotkey(FakeHotkey):
        def start(self):
            self.registration.emit(False, "Shortcut unavailable; it may already be in use.")
            self.finished.emit()

    monkeypatch.setattr(desktop, "SummonHotkeyThread", FailedHotkey)
    window = DesktopWindow()
    try:
        assert app is not None
        window.summon_hotkey_option.setChecked(True)
        app.processEvents()
        assert not window.summon_hotkey_option.isChecked()
        assert window._hotkey_thread is None
        assert "unavailable" in window.summon_hotkey_notice.text()
        assert window._worker is None
    finally:
        window._tray = None
        window.close()


def test_on_non_windows_setting_is_disabled_without_backend_start(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(desktop, "SUMMON_SUPPORTED", False)
    window = DesktopWindow()
    try:
        assert app is not None
        assert not window.summon_hotkey_option.isEnabled()
        assert not window.summon_hotkey_option.isChecked()
        assert window._hotkey_thread is None
    finally:
        window._tray = None
        window.close()
