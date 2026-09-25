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
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
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
from friday.ui.desktop_theme import STYLE
from friday.voice_reminders import VoiceReminderApproval


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
        self.resize(1270, 830)
        self.setMinimumSize(900, 650)
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
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(30_000)
        self._clock_timer.timeout.connect(self._update_local_clock)
        self._clock_timer.start()

    @staticmethod
    def _panel() -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        return panel

    def _build(self, reminders: bool, web: bool) -> None:
        """Six-page dashboard. Each existing live control has exactly one Qt owner."""
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(206)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(13, 22, 13, 18)
        nav.setSpacing(9)
        brand = QLabel("◉  FRIDAY")
        brand.setObjectName("brand")
        nav.addWidget(brand)
        version = QLabel(f"Personal assistant · v{__version__}")
        version.setObjectName("subheading")
        nav.addWidget(version)
        nav.addSpacing(23)
        self.nav_buttons: dict[str, QPushButton] = {}
        self._page_subtitles = {
            "Home": "Your voice assistant and daily essentials",
            "Voice": "One conversation at a time · click to talk",
            "Academic": "Read-only Brightspace calendar",
            "Reminders": "Local reminders and approval-controlled actions",
            "Calendar": "Desktop scheduler and optional Google sync",
            "Settings": "Voice permissions, integrations, and appearance",
        }
        for name in self._page_subtitles:
            button = QPushButton(name)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, destination=name: self._navigate(destination)
            )
            nav.addWidget(button)
            self.nav_buttons[name] = button
        nav.addStretch(1)
        sidebar_note = QLabel(
            "Private controls · visible actions\n"
            "No always-on microphone or persistent conversation memory."
        )
        sidebar_note.setObjectName("subheading")
        sidebar_note.setWordWrap(True)
        nav.addWidget(sidebar_note)
        shell.addWidget(sidebar)

        main = QWidget()
        main.setObjectName("page")
        content = QVBoxLayout(main)
        content.setContentsMargins(17, 15, 17, 13)
        content.setSpacing(13)
        top = QFrame()
        top.setObjectName("topbar")
        bar = QHBoxLayout(top)
        bar.setContentsMargins(15, 10, 15, 10)
        bar.setSpacing(9)
        self.page_title = QLabel("Home")
        self.page_title.setObjectName("pageTitle")
        bar.addWidget(self.page_title, 1)
        self.status = QLabel("Disconnected")
        self.status.setObjectName("status")
        bar.addWidget(self.status)
        self.top_scheduler_status = QLabel("Scheduler: checking…")
        self.top_scheduler_status.setObjectName("chip")
        bar.addWidget(self.top_scheduler_status)
        self.top_academic_status = QLabel("Brightspace: not synced")
        self.top_academic_status.setObjectName("chip")
        bar.addWidget(self.top_academic_status)
        self.clock_status = QLabel()
        self.clock_status.setObjectName("chip")
        bar.addWidget(self.clock_status)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._connect_or_disconnect)
        bar.addWidget(self.connect_button)
        content.addWidget(top)
        self.page_subtitle = QLabel(self._page_subtitles["Home"])
        self.page_subtitle.setObjectName("subheading")
        content.addWidget(self.page_subtitle)

        self.pages = QStackedWidget()
        content.addWidget(self.pages, 1)
        shell.addWidget(main, 1)

        self._build_home_page()
        self._build_voice_page(reminders, web)
        self._build_academic_page()
        self._build_reminders_page()
        self._build_calendar_page()
        self._build_settings_page()
        self._navigate("Home")
        self._update_local_clock()

    def _new_page(self) -> QVBoxLayout:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        canvas = QWidget()
        canvas.setObjectName("page")
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(4, 4, 4, 14)
        layout.setSpacing(13)
        scroll.setWidget(canvas)
        self.pages.addWidget(scroll)
        return layout

    @staticmethod
    def _heading(text: str, *, subtitle: str = "") -> QVBoxLayout:
        box = QVBoxLayout()
        title = QLabel(text)
        title.setObjectName("section")
        box.addWidget(title)
        if subtitle:
            label = QLabel(subtitle)
            label.setObjectName("subheading")
            label.setWordWrap(True)
            box.addWidget(label)
        return box

    @staticmethod
    def _plain_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("detail")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _open_page_button(self, text: str, destination: str) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(
            lambda _checked=False: self._navigate(destination)
        )
        return button

    def _build_home_page(self) -> None:
        page = self._new_page()
        banner = self._panel()
        banner.setObjectName("hero")
        hero = QHBoxLayout(banner)
        hero.setContentsMargins(22, 18, 22, 18)
        greeting = QVBoxLayout()
        greeting_title = QLabel("Welcome back, Tobias")
        greeting_title.setObjectName("heading")
        greeting.addWidget(greeting_title)
        greeting.addWidget(self._plain_label(
            "Your assistant for coursework, everyday reminders, and conversation."
        ))
        hero.addLayout(greeting, 2)
        self.home_voice_state = self._plain_label("Voice is disconnected")
        hero.addWidget(self.home_voice_state, 1)
        hero.addWidget(self._open_page_button("Open voice", "Voice"))
        page.addWidget(banner)

        row = QHBoxLayout()
        row.setSpacing(13)
        left = QVBoxLayout()
        left.setSpacing(13)
        academic = self._panel()
        academic_layout = QVBoxLayout(academic)
        academic_layout.setContentsMargins(17, 15, 17, 15)
        academic_layout.addLayout(self._heading(
            "Brightspace academic calendar",
            subtitle="Read-only · upcoming items from the last valid local snapshot",
        ))
        self.home_academic_status = self._plain_label("Brightspace: not synced")
        academic_layout.addWidget(self.home_academic_status)
        self.home_academic_items = self._plain_label("Sync your feed on the Academic page.")
        academic_layout.addWidget(self.home_academic_items)
        academic_layout.addWidget(self._open_page_button("Open Academic", "Academic"))
        left.addWidget(academic)

        scheduler = self._panel()
        scheduler_layout = QVBoxLayout(scheduler)
        scheduler_layout.setContentsMargins(17, 15, 17, 15)
        scheduler_layout.addLayout(self._heading(
            "Scheduler / Google Calendar",
            subtitle="Existing local reminder worker, with Google sync only by opt-in.",
        ))
        self.home_scheduler_status = self._plain_label("Scheduler: checking…")
        scheduler_layout.addWidget(self.home_scheduler_status)
        scheduler_layout.addWidget(self._open_page_button("Open Calendar", "Calendar"))
        left.addWidget(scheduler)
        row.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(13)
        conversation = self._panel()
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(17, 15, 17, 15)
        conversation_layout.addLayout(self._heading(
            "Current conversation",
            subtitle="Session-only preview. No conversation history is stored.",
        ))
        self.home_recent = self._plain_label("Connect to start a conversation.")
        conversation_layout.addWidget(self.home_recent)
        conversation_layout.addWidget(self._open_page_button("Open Voice", "Voice"))
        right.addWidget(conversation)
        reminders = self._panel()
        reminders_layout = QVBoxLayout(reminders)
        reminders_layout.setContentsMargins(17, 15, 17, 15)
        reminders_layout.addLayout(self._heading("Reminders"))
        self.home_reminder_status = self._plain_label(
            "Use the Reminders page to view pending reminders."
        )
        reminders_layout.addWidget(self.home_reminder_status)
        reminders_layout.addWidget(self._open_page_button("Open Reminders", "Reminders"))
        right.addWidget(reminders)
        row.addLayout(right, 2)
        page.addLayout(row)
        page.addStretch(1)

    def _build_voice_page(self, reminders: bool, web: bool) -> None:
        page = self._new_page()
        row = QHBoxLayout()
        row.setSpacing(13)
        stage = self._panel()
        stage.setObjectName("voiceStage")
        voice = QVBoxLayout(stage)
        voice.setContentsMargins(20, 18, 20, 18)
        voice.setSpacing(12)
        voice.addLayout(self._heading("VOICE SESSION", subtitle="Manual click-to-talk"))
        voice.addStretch(1)
        self.orb = VoiceOrb()
        voice.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.voice_title = QLabel("FRIDAY is offline")
        self.voice_title.setObjectName("section")
        self.voice_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        voice.addWidget(self.voice_title)
        self.voice_hint = self._plain_label(
            "Connect first. Microphone is off until you click."
        )
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
        self.draft_description = self._plain_label("")
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
        row.addWidget(stage, 5)

        transcript_panel = self._panel()
        transcript_layout = QVBoxLayout(transcript_panel)
        transcript_layout.setContentsMargins(15, 16, 15, 15)
        transcript_layout.addLayout(self._heading(
            "CONVERSATION",
            subtitle="Live transcript · current session only · plain text",
        ))
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.document().setMaximumBlockCount(250)
        self.transcript.setPlaceholderText("Your conversation appears here while connected.")
        transcript_layout.addWidget(self.transcript, 1)
        row.addWidget(transcript_panel, 6)
        page.addLayout(row, 1)
        page.addWidget(self._plain_label(
            "FRIDAY uses manual microphone control. No wake word or saved conversation history."
        ))

    def _build_academic_page(self) -> None:
        page = self._new_page()
        academic_panel = self._panel()
        academic_layout = QVBoxLayout(academic_panel)
        academic_layout.setContentsMargins(18, 17, 18, 17)
        academic_layout.setSpacing(11)
        academic_layout.addLayout(self._heading(
            "BRIGHTSPACE",
            subtitle="Published calendar entries only; not a complete assignment or grades API.",
        ))
        self.academic_status = self._plain_label("Brightspace: not synced")
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
        self.academic_sync_button.setObjectName("primary")
        self.academic_sync_button.clicked.connect(self._sync_academic)
        feed_controls.addWidget(self.academic_sync_button)
        self.academic_forget_button = QPushButton("Remove feed")
        self.academic_forget_button.setObjectName("danger")
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
        academic_layout.addWidget(self._plain_label(
            "Due (task) means explicit task DUE. Brightspace-labeled due (event) "
            "reflects the source title, not an independently verified submission deadline."
        ))
        self.academic_list = QListWidget()
        self.academic_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.academic_list.setMinimumHeight(245)
        academic_layout.addWidget(self.academic_list, 1)
        page.addWidget(academic_panel, 1)

    def _build_reminders_page(self) -> None:
        page = self._new_page()
        panel = self._panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 17, 18, 17)
        layout.addLayout(self._heading(
            "UPCOMING REMINDERS",
            subtitle="Locally stored one-time reminders and timers, not Brightspace assignments.",
        ))
        self.reminder_list = QListWidget()
        self.reminder_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reminder_list.setMinimumHeight(220)
        layout.addWidget(self.reminder_list, 1)
        layout.addWidget(self._plain_label(
            "Ask FRIDAY to list, edit, or cancel reminders. Confirm or cancel a draft "
            "on the Voice page; the model cannot approve it on its own."
        ))
        layout.addWidget(self._open_page_button("Open Voice for approvals", "Voice"))
        page.addWidget(panel, 1)

    def _build_calendar_page(self) -> None:
        page = self._new_page()
        panel = self._panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 17, 18, 17)
        layout.addLayout(self._heading(
            "SCHEDULER / GOOGLE CALENDAR",
            subtitle="Starts only when you click; hiding to tray retains an owned worker.",
        ))
        scheduler_controls = QHBoxLayout()
        self.calendar_status = self._plain_label("Calendar: checking worker…")
        scheduler_controls.addWidget(self.calendar_status, 1)
        self.scheduler_button = QPushButton("Start scheduler")
        self.scheduler_button.clicked.connect(self._start_or_stop_scheduler)
        scheduler_controls.addWidget(self.scheduler_button)
        layout.addLayout(scheduler_controls)
        self.scheduler_sync_option = QCheckBox("Sync Google Calendar (iPhone view)")
        self.scheduler_sync_option.setChecked(False)
        layout.addWidget(self.scheduler_sync_option)
        self.scheduler_notice = self._plain_label(
            "The desktop scheduler runs while FRIDAY is open or hidden in the tray."
        )
        layout.addWidget(self.scheduler_notice)
        layout.addWidget(self._plain_label(
            "Google Calendar publishing remains a separate explicit opt-in. "
            "Brightspace data is not automatically copied to Google Calendar."
        ))
        page.addWidget(panel)
        page.addStretch(1)

    def _build_settings_page(self) -> None:
        page = self._new_page()
        voice = self._panel()
        voice_layout = QVBoxLayout(voice)
        voice_layout.addLayout(self._heading(
            "Voice preferences",
            subtitle="Input device and language are currently selected when FRIDAY starts.",
        ))
        voice_layout.addWidget(self._plain_label(
            "Reminder drafting, web search, and microphone controls remain on the Voice page. "
            "Connect only when you are ready."
        ))
        voice_layout.addWidget(self._open_page_button("Open voice controls", "Voice"))
        page.addWidget(voice)
        integrations = self._panel()
        integration_layout = QVBoxLayout(integrations)
        integration_layout.addLayout(self._heading("Integrations"))
        integration_layout.addWidget(self._plain_label(
            "Brightspace credentials are stored in the protected credential vault. "
            "The Calendar page controls the existing scheduler and optional Google sync."
        ))
        integration_layout.addWidget(
            self._open_page_button("Brightspace settings", "Academic")
        )
        integration_layout.addWidget(
            self._open_page_button("Scheduler settings", "Calendar")
        )
        page.addWidget(integrations)
        appearance = self._panel()
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.addLayout(self._heading("Appearance"))
        appearance_layout.addWidget(self._plain_label(
            "Hybrid dark theme · v0.5.5 Slice 1. Custom themes and compact floating "
            "voice mode are not implemented yet."
        ))
        page.addWidget(appearance)
        page.addStretch(1)

    def _navigate(self, destination: str) -> None:
        if destination not in self._page_subtitles:
            raise ValueError("Unknown FRIDAY desktop page")
        index = tuple(self._page_subtitles).index(destination)
        self.pages.setCurrentIndex(index)
        self.page_title.setText(destination)
        self.page_subtitle.setText(self._page_subtitles[destination])
        for name, button in self.nav_buttons.items():
            button.setChecked(name == destination)

    def _update_local_clock(self) -> None:
        self.clock_status.setText(
            datetime.now().astimezone().strftime("%a %b %d · %I:%M %p")
        )

    def _refresh_home_academic(self) -> None:
        """Mirror safe visible labels, never retrieve the private feed."""
        self.home_academic_status.setText(self.academic_status.text())
        items = [
            self.academic_list.item(i).text().replace("\n", " · ")
            for i in range(min(3, self.academic_list.count()))
        ]
        self.home_academic_items.setText(
            "\n".join(items) if items else "No locally cached academic items."
        )

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
        self._clock_timer.stop()
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
        self._clock_timer.stop()
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
            self._refresh_academic_overview()
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
        self._refresh_academic_overview()

    def _refresh_academic_overview(self) -> None:
        status = self.academic_status.text()
        if "failed" in status.lower():
            label = "Brightspace: sync failed (cache retained)"
        elif self._academic_cache_ready:
            label = "Brightspace: cached"
        else:
            label = "Brightspace: not synced"
        self.top_academic_status.setText(label)
        self._refresh_home_academic()

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
        self._refresh_academic_overview()

    def _sync_academic(self) -> None:
        if self._academic_sync is not None or self._quitting or self._closing:
            return
        thread = AcademicSyncThread()
        thread.completed.connect(self._academic_completed)
        thread.failed.connect(self._academic_failed)
        thread.finished.connect(self._academic_finished)
        self._academic_sync = thread
        self.academic_status.setText("Brightspace: synchronizing…")
        self._refresh_academic_overview()
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
        self._refresh_academic_overview()

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
        self.top_scheduler_status.setText(
            "Scheduler: " + label.removeprefix("Calendar: ")
        )
        self.home_scheduler_status.setText(label)
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
        self.home_scheduler_status.setText(message)

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
        self.home_voice_state.setText(f"Voice · {state}")
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
        if draft is not None:
            # Keep the only approval controls visible without duplicating actions.
            self._navigate("Voice")

    def _on_event(self, kind: str, value: Any) -> None:
        if kind == "status":
            self._set_state(str(value))
        elif kind == "recovery":
            if value in {"connection", "audio", "expired"}:
                self._last_recovery = value
        elif kind == "transcript":
            speaker = value.get("speaker")
            text = value.get("text", "")
            self._append("You" if speaker == "user" else "FRIDAY", text)
            if isinstance(text, str) and text.strip():
                prefix = "You" if speaker == "user" else "FRIDAY"
                self.home_recent.setText(f"{prefix}: {text.strip()[:240]}")
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
            self.home_reminder_status.setText(
                f"{len(value)} pending reminder(s) reported by the current voice session."
            )
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
        self._clock_timer.stop()
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
