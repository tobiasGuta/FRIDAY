"""Opt-in PySide6 desktop shell. Only this module imports Qt.

Gemini, SQLite writes, and PortAudio run on a dedicated worker thread. Qt
widgets receive only queued signals and never process raw PCM or API secrets.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import threading
from datetime import datetime
from typing import Any

from PySide6.QtCore import QPointF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from friday import __version__
from friday.audio.devices import AudioDeviceError, Microphone, Speaker
from friday.brightspace_calendar import (
    AcademicStore,
    BrightspaceError,
    display_time,
    source_labeled_due,
)
from friday.brightspace_feed import forget_feed, save_feed
from friday.config import Settings
from friday.core.session import SessionError, SessionManager
from friday.providers.gemini_live import GeminiLiveProvider
from friday.schedule import ScheduleStore, WorkerHealth, read_worker_health
from friday.ui.brightspace_worker import AcademicSyncThread
from friday.ui.desktop_scheduler import AlertRequest, SchedulerThread
from friday.ui.desktop_session import DesktopVoiceSession
from friday.voice_reminders import VoiceReminderApproval

STYLE = """
QMainWindow, QWidget#root { background-color: #0C1220; color: #EEF5FF; }
QFrame#panel { background-color: #151E31; border: 1px solid #283A58;
               border-radius: 18px; }
QFrame#approval { background-color: #24243F; border: 1px solid #6264A8;
                  border-radius: 14px; }
QLabel#heading { color: #F6FAFF; font-size: 25px; font-weight: bold; }
QLabel#subheading { color: #99ADC9; font-size: 12px; }
QLabel#status { color: #76E9CB; font-weight: bold; font-size: 13px; }
QLabel#section { color: #F1F5FF; font-size: 15px; font-weight: bold; }
QLabel#approvalTitle { color: #E2D9FF; font-weight: bold; font-size: 15px; }
QLabel#detail { color: #C8D4E9; font-size: 13px; }
QPushButton { border: 1px solid #3D5371; border-radius: 10px;
              background: #233450; color: #EAF1FF; padding: 9px 14px;
              font-size: 13px; font-weight: bold; }
QPushButton:hover { background: #354D70; }
QPushButton:disabled { background: #1C2637; color: #63758E;
                       border-color: #28364B; }
QPushButton#primary { background: #477CE4; color: white; border: 0; }
QPushButton#primary:hover { background: #6596F2; }
QPushButton#approve { background: #287B70; color: white; border: 0; }
QPushButton#reject { background: #633A58; color: white; border: 0; }
QPlainTextEdit, QListWidget { background: #10192A; border: 1px solid #2A3A54;
                             color: #E0EAFA; border-radius: 11px; padding: 10px;
                             font-size: 13px; }
QCheckBox { color: #B7C8E0; spacing: 7px; font-size: 12px; }
QCheckBox:disabled { color: #687B95; }
QLineEdit { background: #10192A; border: 1px solid #2A3A54; color: #E0EAFA;
            border-radius: 9px; padding: 7px 10px; }
QLineEdit:disabled { color: #687B95; }
QTabWidget::pane { border: 1px solid #283A58; border-radius: 9px; }
QTabBar::tab { background: #151E31; color: #B7C8E0; border: 1px solid #283A58;
               padding: 7px 15px; }
QTabBar::tab:selected { background: #233450; color: #EEF5FF; }
"""


def _formatted_at(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%a, %b %d · %I:%M %p")
    except ValueError:
        return value


def _app_icon() -> QIcon:
    """Paint a small, self-contained icon without external asset dependencies."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#477CE4"))
    painter.drawEllipse(3, 3, 58, 58)
    painter.setPen(QColor("#FFFFFF"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(36)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "F")
    painter.end()
    return QIcon(pixmap)


def _calendar_label(health: WorkerHealth) -> str:
    if not health.running:
        return "Calendar: worker not running"
    if not health.calendar_enabled:
        return (
            "Calendar: worker active (sync mode unknown)"
            if health.calendar_state == "unknown" else "Calendar: worker is local-only"
        )
    if health.calendar_state == "waiting":
        return "Calendar: waiting for first sync"
    if health.calendar_state == "syncing":
        return "Calendar: syncing…"
    if health.last_success is not None:
        last = datetime.fromtimestamp(health.last_success).strftime("%I:%M %p")
        if health.calendar_state == "ok":
            return f"Calendar: sync OK · last success {last}"
        return f"Calendar: sync failed · last success {last}"
    return "Calendar: sync failed" if health.calendar_state == "error" else (
        "Calendar: status unavailable"
    )


class VoiceOrb(QWidget):
    """A light-weight painted indicator; no media, microphone, or model ownership."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(184, 184)
        self._phase = 0.0
        self._state = "Disconnected"
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def _animate(self) -> None:
        if self._state in {"Listening", "Responding", "Connecting"}:
            self._phase += 0.12
            self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        colors = {
            "Listening": QColor("#54DFC4"),
            "Responding": QColor("#A18BFF"),
            "Ready": QColor("#65A8FF"),
            "Connecting": QColor("#85A6DC"),
        }
        color = colors.get(self._state, QColor("#4D627F"))
        center = QPointF(self.width() / 2, self.height() / 2)
        pulse = 5.0 * (1.0 + math.sin(self._phase)) if self._state in {
            "Listening", "Responding", "Connecting"
        } else 0.0
        for radius, alpha in ((76 + pulse, 25), (63 + pulse * 0.6, 48)):
            ring = QColor(color)
            ring.setAlpha(alpha)
            painter.setBrush(ring)
            painter.drawEllipse(center, radius, radius)
        gradient = QRadialGradient(center, 49)
        gradient.setColorAt(0, QColor("#EDF9FF"))
        gradient.setColorAt(0.25, color.lighter(150))
        gradient.setColorAt(1, color.darker(125))
        painter.setBrush(gradient)
        painter.drawEllipse(center, 46, 46)
        painter.end()


class DesktopThread(QThread):
    """Isolate a complete asyncio session from the Qt main thread."""

    message = Signal(str, object)

    def __init__(
        self,
        *,
        input_device: int | None,
        output_device: int | None,
        input_language: str,
        reminders: bool,
        web: bool,
        max_seconds: int | None,
        academic: bool = False,
    ) -> None:
        super().__init__()
        self.academic = academic
        self.input_device = input_device
        self.output_device = output_device
        self.input_language = input_language
        self.reminders = reminders
        self.web = web
        self.max_seconds = max_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: DesktopVoiceSession | None = None
        self._quit_requested = threading.Event()

    def request(self, command: str) -> None:
        if command == "quit":
            self._quit_requested.set()
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._offer, command)
        except RuntimeError:
            pass  # Already shutting down.

    def _offer(self, command: str) -> None:
        if self._session is not None:
            self._session.request(command)

    async def _run_session(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            if self._quit_requested.is_set():
                return
            settings = Settings()
            settings.require_gemini_key()
            if self.web and settings.search_backend == "tavily":
                settings.require_tavily_key()
            speaker = Speaker(
                sample_rate=settings.output_sample_rate, device=self.output_device
            )
            microphone = Microphone(
                loop=self._loop, sample_rate=settings.input_sample_rate,
                device=self.input_device,
            )
            approval = VoiceReminderApproval(ScheduleStore()) if self.reminders else None
            manager = SessionManager(
                GeminiLiveProvider(
                    settings, manual_activity=True, enable_local_clock=True,
                    enable_web_search=self.web, enable_weather=True,
                    input_language=(
                        None if self.input_language == "auto" else self.input_language
                    ),
                    reminder_approval=approval,
                    enable_academic_calendar=self.academic,
                ),
                queue_size=settings.event_queue_size,
            )
            self._session = DesktopVoiceSession(
                manager, microphone, speaker, approval=approval, emit=self.message.emit,
                max_seconds=self.max_seconds or settings.max_session_seconds,
            )
            if self._quit_requested.is_set():
                return
            await self._session.run()
        finally:
            self._session = None
            self._loop = None

    def run(self) -> None:
        try:
            asyncio.run(self._run_session())
        except AudioDeviceError:
            self.message.emit(
                "error", "Audio device unavailable. Check connections and permissions."
            )
            if not self._quit_requested.is_set():
                self.message.emit("recovery", "audio")
        except (ValueError, SessionError):
            self.message.emit("error", "Voice connection could not start. Check configuration.")
            if not self._quit_requested.is_set():
                self.message.emit("recovery", "connection")
        except Exception as exc:
            # Keep provider errors, payloads and API secrets out of diagnostics.
            self.message.emit("error", f"Desktop session stopped ({type(exc).__name__}).")
            if not self._quit_requested.is_set():
                self.message.emit("recovery", "connection")
        finally:
            self.message.emit("status", "Disconnected")


class DesktopWindow(QMainWindow):
    def __init__(
        self,
        *,
        input_device: int | None = None,
        output_device: int | None = None,
        input_language: str = "en-US",
        reminders: bool = True,
        web: bool = False,
        max_seconds: int | None = None,
    ) -> None:
        super().__init__()
        self._input_device = input_device
        self._output_device = output_device
        self._input_language = input_language
        self._max_seconds = max_seconds
        self._worker: DesktopThread | None = None
        self._scheduler: SchedulerThread | None = None
        self._academic_sync: AcademicSyncThread | None = None
        self._academic_synced_session = False
        self._academic_cache_ready = False
        self._scheduler_stop_requested = False
        self._scheduler_had_error = False
        self._closing = False
        self._quitting = False
        self._tray: QSystemTrayIcon | None = None
        self._state = "Disconnected"
        self._last_recovery: str | None = None
        self._draft: dict[str, str] | None = None
        self.setWindowTitle(f"FRIDAY · v{__version__}")
        self.resize(990, 740)
        self.setMinimumSize(790, 620)
        self.setStyleSheet(STYLE)
        self.setWindowIcon(_app_icon())
        self._build(reminders, web)
        self._set_state("Disconnected")
        self._init_tray()
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._refresh_worker_status)
        self._refresh_worker_status()
        self._health_timer.start()
        self._academic_timer = QTimer(self)
        self._academic_timer.setInterval(30 * 60 * 1000)
        self._academic_timer.timeout.connect(self._auto_sync_brightspace)
        self._academic_timer.start()
        self._display_academic_cached()

    @staticmethod
    def _panel() -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        return panel

    def _build(self, reminders: bool, web: bool) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(25, 22, 25, 20)
        outer.setSpacing(16)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        heading = QLabel("FRIDAY")
        heading.setObjectName("heading")
        sub = QLabel("Your personal voice assistant  ·  Private controls, visible actions")
        sub.setObjectName("subheading")
        brand.addWidget(heading)
        brand.addWidget(sub)
        header.addLayout(brand, 1)
        self.status = QLabel("Disconnected")
        self.status.setObjectName("status")
        header.addWidget(self.status)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._connect_or_disconnect)
        header.addWidget(self.connect_button)
        outer.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(16)
        voice_panel = self._panel()
        voice = QVBoxLayout(voice_panel)
        voice.setContentsMargins(20, 18, 20, 18)
        voice.setSpacing(12)
        voice_header = QLabel("VOICE")
        voice_header.setObjectName("section")
        voice.addWidget(voice_header)
        voice.addStretch(1)
        self.orb = VoiceOrb()
        voice.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.voice_title = QLabel("FRIDAY is offline")
        self.voice_title.setObjectName("section")
        self.voice_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        voice.addWidget(self.voice_title)
        self.voice_hint = QLabel("Connect first. Microphone is off until you click.")
        self.voice_hint.setObjectName("subheading")
        self.voice_hint.setWordWrap(True)
        self.voice_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        voice.addWidget(self.voice_hint)
        self.mic_button = QPushButton("Start talking")
        self.mic_button.setObjectName("primary")
        self.mic_button.setMinimumHeight(53)
        self.mic_button.clicked.connect(self._toggle_microphone)
        voice.addWidget(self.mic_button)
        voice.addStretch(1)

        self.approval_panel = QFrame()
        self.approval_panel.setObjectName("approval")
        approval_layout = QVBoxLayout(self.approval_panel)
        approval_title = QLabel("Approval required")
        approval_title.setObjectName("approvalTitle")
        approval_layout.addWidget(approval_title)
        self.draft_description = QLabel("")
        self.draft_description.setObjectName("detail")
        self.draft_description.setWordWrap(True)
        approval_layout.addWidget(self.draft_description)
        choice = QHBoxLayout()
        self.approve_button = QPushButton("Confirm")
        self.approve_button.setObjectName("approve")
        self.approve_button.clicked.connect(lambda: self._send("approve"))
        self.reject_button = QPushButton("Cancel")
        self.reject_button.setObjectName("reject")
        self.reject_button.clicked.connect(lambda: self._send("reject"))
        choice.addWidget(self.approve_button)
        choice.addWidget(self.reject_button)
        approval_layout.addLayout(choice)
        voice.addWidget(self.approval_panel)
        self.approval_panel.hide()

        self.reminder_option = QCheckBox("Enable reminder drafts and approval")
        self.reminder_option.setChecked(reminders)
        self.web_option = QCheckBox("Enable web search (uses API credits)")
        self.web_option.setChecked(web)
        voice.addWidget(self.reminder_option)
        voice.addWidget(self.web_option)
        content.addWidget(voice_panel, 5)

        sidebar = QVBoxLayout()
        transcript_panel = self._panel()
        transcript_layout = QVBoxLayout(transcript_panel)
        transcript_layout.setContentsMargins(15, 16, 15, 15)
        section = QLabel("CONVERSATION")
        section.setObjectName("section")
        transcript_layout.addWidget(section)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.document().setMaximumBlockCount(250)
        self.transcript.setPlaceholderText("Your conversation appears here while connected.")
        transcript_layout.addWidget(self.transcript)
        sidebar.addWidget(transcript_panel, 3)

        reminders_panel = self._panel()
        reminders_layout = QVBoxLayout(reminders_panel)
        reminders_layout.setContentsMargins(15, 15, 15, 15)
        section = QLabel("UPCOMING REMINDERS")
        section.setObjectName("section")
        reminders_layout.addWidget(section)
        self.reminder_list = QListWidget()
        self.reminder_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reminders_layout.addWidget(self.reminder_list)
        tabs = QTabWidget()
        tabs.addTab(reminders_panel, "Reminders")
        academic_panel = self._panel()
        academic_layout = QVBoxLayout(academic_panel)
        academic_layout.setContentsMargins(12, 10, 12, 10)
        academic_layout.setSpacing(7)
        self.academic_status = QLabel("Brightspace: not synced")
        self.academic_status.setObjectName("subheading")
        self.academic_status.setWordWrap(True)
        academic_layout.addWidget(self.academic_status)
        self.academic_feed_input = QLineEdit()
        self.academic_feed_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.academic_feed_input.setPlaceholderText("Paste private feed URL locally")
        self.academic_feed_input.setToolTip(
            "Saved to protected system storage; never sent to Gemini or logged."
        )
        academic_layout.addWidget(self.academic_feed_input)
        feed_controls = QHBoxLayout()
        self.academic_save_button = QPushButton("Save feed")
        self.academic_save_button.clicked.connect(self._save_academic_feed)
        feed_controls.addWidget(self.academic_save_button)
        self.academic_sync_button = QPushButton("Sync now")
        self.academic_sync_button.clicked.connect(self._sync_academic)
        feed_controls.addWidget(self.academic_sync_button)
        self.academic_forget_button = QPushButton("Remove feed")
        self.academic_forget_button.clicked.connect(self._forget_academic_feed)
        feed_controls.addWidget(self.academic_forget_button)
        academic_layout.addLayout(feed_controls)
        self.academic_auto_option = QCheckBox("Refresh every 30 min while scheduler runs")
        self.academic_auto_option.setChecked(False)
        academic_layout.addWidget(self.academic_auto_option)
        self.academic_voice_option = QCheckBox(
            "Enable read-only academic voice lookup (next connection)"
        )
        self.academic_voice_option.setChecked(False)
        academic_layout.addWidget(self.academic_voice_option)
        self.academic_list = QListWidget()
        self.academic_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        academic_layout.addWidget(self.academic_list, 1)
        tabs.addTab(academic_panel, "Brightspace")
        sidebar.addWidget(tabs, 3)
        content.addLayout(sidebar, 6)
        outer.addLayout(content, 1)

        scheduler_controls = QHBoxLayout()
        self.calendar_status = QLabel("Calendar: checking worker…")
        self.calendar_status.setObjectName("detail")
        scheduler_controls.addWidget(self.calendar_status, 1)
        self.scheduler_button = QPushButton("Start scheduler")
        self.scheduler_button.clicked.connect(self._start_or_stop_scheduler)
        scheduler_controls.addWidget(self.scheduler_button)
        outer.addLayout(scheduler_controls)
        self.scheduler_sync_option = QCheckBox("Sync Google Calendar (iPhone view)")
        self.scheduler_sync_option.setChecked(False)  # Explicit opt-in, as in the CLI.
        outer.addWidget(self.scheduler_sync_option)
        self.scheduler_notice = QLabel(
            "The desktop scheduler runs while FRIDAY is open or hidden in the tray."
        )
        self.scheduler_notice.setObjectName("subheading")
        self.scheduler_notice.setWordWrap(True)
        outer.addWidget(self.scheduler_notice)
        footer = QLabel(
            "Click-to-talk · No wake word or persistent conversation memory · "
            "Quitting FRIDAY stops its desktop-managed scheduler."
        )
        footer.setObjectName("subheading")
        footer.setWordWrap(True)
        outer.addWidget(footer)

    def _init_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu(self)
        menu.addAction("Open FRIDAY", self._open_window)
        menu.addAction("Hide FRIDAY", self.hide)
        menu.addSeparator()
        menu.addAction("Quit FRIDAY", self._quit_application)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        self._tray = tray
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)
        tray.show()
        self._update_tray_tooltip()

    def _update_tray_tooltip(self) -> None:
        if self._tray is not None:
            self._tray.setToolTip(
                f"FRIDAY · {self._state}\n{self.calendar_status.text()}"
            )

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._open_window()

    def _open_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_application(self) -> None:
        """Only the explicit tray Quit shuts down the retained voice session."""
        if self._quitting:
            return
        self._quitting = True
        self._health_timer.stop()
        self._academic_timer.stop()
        if self._scheduler is not None:
            self._scheduler.request_stop()
            self._scheduler_stop_requested = True
        if self._worker is not None:
            self._worker.request("quit")
            self._set_state("Disconnecting")
        self._finish_quit()

    def _finish_quit(self) -> None:
        if (
            self._worker is not None or self._scheduler is not None
            or self._academic_sync is not None
        ):
            return
        self._health_timer.stop()
        self._academic_timer.stop()
        if self._tray is not None:
            self._tray.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _display_academic_cached(self) -> None:
        """Read local cache only; never fetch Brightspace on desktop startup."""
        try:
            store = AcademicStore()
            snapshot = store.upcoming(days=7, limit=12)
            last = snapshot.last_success
        except (BrightspaceError, OSError, ValueError):
            self.academic_status.setText("Brightspace: local cache unavailable")
            self.academic_list.clear()
            self._academic_cache_ready = False
            self.academic_voice_option.setEnabled(False)
            return
        self._academic_cache_ready = bool(last)
        self.academic_list.clear()
        for item in snapshot.items:
            label = (
                "Due (task)"
                if item.explicit_due else (
                    "Brightspace-labeled due (event)"
                    if source_labeled_due(item) else "Scheduled"
                )
            )
            recurrence = " · recurring series (not expanded)" if item.recurring else ""
            self.academic_list.addItem(
                f"{label}: {item.title}\n{display_time(item)}{recurrence}"
            )
        if not snapshot.items:
            self.academic_list.addItem("No upcoming published calendar items")
        if last:
            try:
                stamp = datetime.fromisoformat(last).astimezone().strftime("%I:%M %p")
                self.academic_status.setText(
                    f"Brightspace: cached · last sync {stamp} · feed may omit assignments"
                )
            except ValueError:
                self.academic_status.setText("Brightspace: cached; sync timestamp unavailable")
        else:
            self.academic_status.setText("Brightspace: not synced")
        self._set_state(self._state)
        if not last:
            self.academic_voice_option.setChecked(False)

    def _save_academic_feed(self) -> None:
        if self._academic_sync is not None or self._quitting or self._closing:
            return
        entered = self.academic_feed_input.text()
        # Never emit the URL into any UI notice, log, transcript, or exception.
        self.academic_feed_input.clear()
        try:
            save_feed(entered)
        except BrightspaceError as exc:
            self.academic_status.setText(str(exc))
            return
        self._academic_synced_session = False
        self.academic_auto_option.setChecked(False)
        self.academic_voice_option.setChecked(False)
        self._display_academic_cached()
        self.academic_status.setText(
            "Brightspace feed saved in protected storage. Click Sync now."
        )

    def _sync_academic(self) -> None:
        if self._academic_sync is not None or self._quitting or self._closing:
            return
        thread = AcademicSyncThread()
        thread.completed.connect(self._academic_completed)
        thread.failed.connect(self._academic_failed)
        thread.finished.connect(self._academic_finished)
        self._academic_sync = thread
        self.academic_status.setText("Brightspace: synchronizing…")
        self._academic_controls_enabled(False)
        thread.start()

    def _academic_controls_enabled(self, enabled: bool) -> None:
        active = enabled and not self._quitting and not self._closing
        for widget in (
            self.academic_save_button, self.academic_sync_button,
            self.academic_forget_button, self.academic_feed_input,
        ):
            widget.setEnabled(active)

    def _academic_completed(self, _snapshot: object) -> None:
        self._academic_synced_session = True
        self._display_academic_cached()

    def _academic_failed(self, message: str) -> None:
        self._display_academic_cached()
        self.academic_status.setText(
            f"Brightspace sync failed: {message} Previous cached data, if any, is unchanged."
        )

    def _academic_finished(self) -> None:
        if self._academic_sync is not None:
            self._academic_sync.deleteLater()
            self._academic_sync = None
        self._academic_controls_enabled(True)
        if self._quitting:
            self._finish_quit()
        elif self._closing and self._worker is None and self._scheduler is None:
            self.close()

    def _auto_sync_brightspace(self) -> None:
        if (
            self.academic_auto_option.isChecked() and self._academic_synced_session
            and self._scheduler is not None and not self._scheduler_stop_requested
            and self._academic_sync is None and not self._quitting
        ):
            self._sync_academic()

    def _forget_academic_feed(self) -> None:
        if self._academic_sync is not None or self._quitting or self._closing:
            return
        answer = QMessageBox.question(
            self, "Remove Brightspace feed",
            "Remove the protected feed credential and locally cached academic events?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            forget_feed()
        except BrightspaceError as exc:
            self.academic_status.setText(str(exc))
            return
        self._academic_synced_session = False
        self.academic_auto_option.setChecked(False)
        self.academic_voice_option.setChecked(False)
        self.academic_feed_input.clear()
        self._display_academic_cached()

    def _tray_notifications_available(self) -> bool:
        return (
            self._tray is not None and self._tray.isVisible()
            and QSystemTrayIcon.supportsMessages()
        )

    def _refresh_worker_status(self) -> None:
        try:
            health = read_worker_health()
            label = _calendar_label(health)
        except (OSError, sqlite3.Error):
            health = None
            label = "Calendar: status unavailable"
        if self._scheduler is not None:
            if self._scheduler_stop_requested:
                label = "Calendar: stopping desktop scheduler…"
            elif health is not None and not health.running:
                label = "Calendar: starting desktop scheduler…"
            self.scheduler_button.setText("Stop scheduler")
            self.scheduler_button.setEnabled(
                not self._scheduler_stop_requested and not self._quitting
            )
            self.scheduler_sync_option.setEnabled(False)
        elif health is None:
            self.scheduler_button.setText("Scheduler status unavailable")
            self.scheduler_button.setEnabled(False)
            self.scheduler_sync_option.setEnabled(False)
        elif health.running:
            self.scheduler_button.setText("Worker running externally")
            self.scheduler_button.setEnabled(False)
            self.scheduler_sync_option.setEnabled(False)
        else:
            self.scheduler_button.setText("Start scheduler")
            available = self._tray_notifications_available()
            self.scheduler_button.setEnabled(
                available and not self._quitting and not self._closing
            )
            self.scheduler_sync_option.setEnabled(
                available and not self._quitting and not self._closing
            )
            if not available and not self._quitting:
                self.scheduler_notice.setText(
                    "System tray notifications are needed for a desktop scheduler. "
                    "Use the existing foreground CLI worker on this platform."
                )
        self.calendar_status.setText(label)
        self._update_tray_tooltip()

    def _start_or_stop_scheduler(self) -> None:
        if self._scheduler is not None:
            self._scheduler_stop_requested = True
            self._scheduler.request_stop()
            self._refresh_worker_status()
            return
        if self._quitting or self._closing:
            return
        try:
            if read_worker_health().running:
                self._refresh_worker_status()
                return
        except (OSError, sqlite3.Error):
            self._refresh_worker_status()
            return
        if not self._tray_notifications_available():
            self._refresh_worker_status()
            return
        thread = SchedulerThread(calendar_enabled=self.scheduler_sync_option.isChecked())
        thread.alert.connect(self._show_scheduler_alert)
        thread.notice.connect(self._scheduler_notice_received)
        thread.finished.connect(self._scheduler_finished)
        self._scheduler = thread
        self._scheduler_had_error = False
        self.scheduler_notice.setText(
            "Scheduler running in FRIDAY. Hide the window to keep it active."
        )
        self._refresh_worker_status()
        thread.start()

    def _show_scheduler_alert(self, alert: AlertRequest) -> None:
        try:
            if self._tray_notifications_available():
                title = "FRIDAY reminder" if alert.kind == "reminder" else "FRIDAY timer"
                self._tray.showMessage(
                    title, alert.text, QSystemTrayIcon.MessageIcon.Information, 10000
                )
                alert.attempted = True
        finally:
            alert.done.set()

    def _scheduler_notice_received(self, message: str) -> None:
        self._scheduler_had_error = True
        self.scheduler_notice.setText(message)

    def _scheduler_finished(self) -> None:
        if self._scheduler is not None:
            self._scheduler.deleteLater()
            self._scheduler = None
        self._scheduler_stop_requested = False
        if not self._scheduler_had_error:
            self.scheduler_notice.setText(
                "Scheduler stopped. Reminders require a running worker to be delivered."
            )
        self._refresh_worker_status()
        if self._quitting:
            self._finish_quit()
        elif self._closing and self._worker is None:
            self.close()

    def _append(self, label: str, text: str) -> None:
        if text:
            self.transcript.appendPlainText(f"{label}: {text}")

    def _set_state(self, state: str) -> None:
        self._state = state
        self.status.setText(state)
        self.orb.set_state(state)
        self._update_tray_tooltip()
        captions = {
            "Disconnected": ("FRIDAY is offline", "Connect when you're ready."),
            "Connecting": ("Connecting to FRIDAY", "Opening audio and Gemini Live…"),
            "Ready": ("How can I help you?", "Press the button, speak, then press it again."),
            "Listening": ("I'm listening", "Press Stop recording when you're finished."),
            "Responding": ("FRIDAY is responding", "Wait until her reply has finished."),
            "Disconnecting": ("Disconnecting", "Closing microphone and connection."),
            "Connection failed": ("Connection failed", "See the conversation panel."),
            "Connection lost": (
                "Voice connection ended", "Check your network; reconnect when ready."
            ),
            "Audio unavailable": (
                "Audio device unavailable", "Check microphone and speaker, then reconnect."
            ),
            "Session expired": (
                "Session time limit reached", "Reconnect to start a fresh voice session."
            ),
        }
        title, hint = captions.get(state, (state, ""))
        self.voice_title.setText(title)
        self.voice_hint.setText(hint)
        ready = state == "Ready"
        recoverable = {"Connection lost", "Audio unavailable", "Session expired"}
        self.mic_button.setEnabled(ready or state == "Listening")
        self.mic_button.setText("Stop recording" if state == "Listening" else "Start talking")
        self.connect_button.setText(
            "Reconnect" if state in recoverable else (
                "Connect" if state in {"Disconnected", "Connection failed"} else "Disconnect"
            )
        )
        self.connect_button.setEnabled(state not in {"Disconnecting"})
        self.reminder_option.setEnabled(
            state in {"Disconnected", "Connection failed"} or state in recoverable
        )
        self.web_option.setEnabled(
            state in {"Disconnected", "Connection failed"} or state in recoverable
        )
        self.approve_button.setEnabled(ready and self._draft is not None)
        self.reject_button.setEnabled(ready and self._draft is not None)
        self.academic_voice_option.setEnabled(
            self._academic_cache_ready
            and state in {"Disconnected", "Connection failed", *recoverable}
            and not self._quitting and not self._closing
        )

    def _connect_or_disconnect(self) -> None:
        if self._worker is not None:
            self._worker.request("quit")
            self._set_state("Disconnecting")
            return
        if self._closing or self._quitting:
            return
        if self._last_recovery is not None:
            self._append(
                "System", "Starting a new voice session. Previous Live context is not restored."
            )
        self._last_recovery = None
        worker = DesktopThread(
            input_device=self._input_device, output_device=self._output_device,
            input_language=self._input_language,
            reminders=self.reminder_option.isChecked(), web=self.web_option.isChecked(),
            max_seconds=self._max_seconds,
            academic=self.academic_voice_option.isChecked(),
        )
        self._worker = worker
        worker.message.connect(self._on_event)
        worker.finished.connect(self._worker_finished)
        self._set_state("Connecting")
        worker.start()

    def _toggle_microphone(self) -> None:
        if self._state == "Ready":
            self._send("start")
        elif self._state == "Listening":
            self._send("stop")
        self.mic_button.setEnabled(False)  # Prevent rapid double-click toggles.

    def _send(self, command: str) -> None:
        if self._worker is not None:
            self._worker.request(command)
        if command in {"approve", "reject"}:
            self.approve_button.setEnabled(False)
            self.reject_button.setEnabled(False)

    def _show_draft(self, draft: dict[str, str] | None) -> None:
        self._draft = draft
        self.approval_panel.setVisible(draft is not None)
        if draft is not None:
            action = draft["action"]
            if action == "edit":
                description = (
                    f"Change: {draft['before_text']} · {_formatted_at(draft['before_at'])}\n"
                    f"To: {draft['text']} · {_formatted_at(draft['at'])}"
                )
            elif action == "cancel":
                description = (
                    f"Cancel: {draft['text']} · {_formatted_at(draft['at'])}"
                )
            else:
                description = (
                    f"Create: {draft['text']} · {_formatted_at(draft['at'])}"
                )
            self.draft_description.setText(
                description + "\nNothing changes until you confirm."
            )
        self._set_state(self._state)

    def _on_event(self, kind: str, value: Any) -> None:
        if kind == "status":
            self._set_state(str(value))
        elif kind == "recovery":
            if value in {"connection", "audio", "expired"}:
                self._last_recovery = value
        elif kind == "transcript":
            speaker = value.get("speaker")
            self._append("You" if speaker == "user" else "FRIDAY", value.get("text", ""))
        elif kind == "notice":
            self._append("System", str(value))
        elif kind == "error":
            self._append("Error", str(value))
        elif kind == "draft":
            self._show_draft(value)
        elif kind == "reminders":
            self.reminder_list.clear()
            for item in value:
                self.reminder_list.addItem(
                    f"{item['text']}\n{_formatted_at(item['at'])}"
                )
            if not value:
                self.reminder_list.addItem("No pending reminders")
        elif kind == "sources":
            for item in value:
                self._append("Source", f"{item['title']} — {item['url']}")
        elif kind == "result":
            status = value.get("status", "error")
            if status in {"created", "updated", "cancelled"}:
                self._append(
                    "Confirmed",
                    f"Reminder {status}: {value['text']} · {_formatted_at(value['at'])}",
                )
            elif status == "rejected":
                self._append("Confirmed", "Draft discarded; no changes saved.")
            else:
                self._append(
                    "Not saved", str(value.get("error", "Unable to apply the draft"))
                )

    def _worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._show_draft(None)
        states = {
            "connection": "Connection lost",
            "audio": "Audio unavailable",
            "expired": "Session expired",
        }
        self._set_state(
            "Disconnected" if self._closing or self._quitting
            else states.get(self._last_recovery, "Disconnected")
        )
        if self._quitting:
            self._finish_quit()
        elif self._closing and self._scheduler is None:
            self.close()

    def closeEvent(self, event: Any) -> None:
        if self._tray is not None and self._tray.isVisible() and not self._quitting:
            self.hide()
            event.ignore()
            return
        if (
            self._worker is not None or self._scheduler is not None
            or self._academic_sync is not None
        ):
            self._closing = True
            if self._worker is not None:
                self._worker.request("quit")
                self._set_state("Disconnecting")
            if self._scheduler is not None:
                self._scheduler_stop_requested = True
                self._scheduler.request_stop()
            event.ignore()
            return
        self._health_timer.stop()
        self._academic_timer.stop()
        event.accept()


def launch_desktop(
    *,
    input_device: int | None = None,
    output_device: int | None = None,
    input_language: str = "en-US",
    reminders: bool = True,
    web: bool = False,
    max_seconds: int | None = None,
) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setApplicationName("FRIDAY")
    window = DesktopWindow(
        input_device=input_device, output_device=output_device,
        input_language=input_language, reminders=reminders, web=web,
        max_seconds=max_seconds,
    )
    window.show()
    return app.exec()
