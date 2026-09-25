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

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
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
    AcademicSnapshot,
    AcademicStore,
    BrightspaceError,
    display_time,
    source_labeled_due,
)
from friday.brightspace_feed import forget_feed, save_feed
from friday.config import Settings
from friday.core.session import SessionError, SessionManager
from friday.providers.gemini_live import GeminiLiveProvider
from friday.schedule import (
    ScheduleStore,
    WorkerHealth,
    read_pending_reminder_preview,
    read_worker_health,
)
from friday.ui.brightspace_worker import AcademicSyncThread
from friday.ui.desktop_scheduler import AlertRequest, SchedulerThread
from friday.ui.desktop_session import DesktopVoiceSession
from friday.ui.desktop_theme import STYLE
from friday.ui.focus_context import focus_ui_target
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
    """A visual indicator of *actual* session states, never an audio-level meter.

    Animation is local Qt paint work; it never opens audio or a model connection.
    The cinematic variant is larger and has decorative rotating arcs.
    """

    ACTIVE_STATES = frozenset({"Listening", "Responding", "Connecting"})

    def __init__(
        self, parent: QWidget | None = None, *, diameter: int = 184,
        cinematic: bool = False,
        hologram: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._cinematic = cinematic
        self._hologram = hologram
        self._phase = 0.0
        self._state = "Disconnected"
        self._timer = QTimer(self)
        self._timer.setInterval(60 if cinematic else 70)
        self._timer.timeout.connect(self._animate)
        self._timer.start()
        self.setAccessibleName("FRIDAY voice state orb")
        self.setAccessibleDescription(
            "Decorative animation reflects the connection, listening, or responding state."
        )

    def set_state(self, state: str) -> None:
        self._state = state
        self.setAccessibleDescription(f"FRIDAY voice state: {state}.")
        self.update()

    def _animate(self) -> None:
        if self._state in self.ACTIVE_STATES and self.isVisible():
            self._phase = (self._phase + 0.095) % (2 * math.pi)
            self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 184, self.height() / 184)
        if self._hologram:
            self._paint_hologram(painter)
            painter.end()
            return
        colors = {
            "Listening": QColor("#54DFC4"),
            "Responding": QColor("#A18BFF"),
            "Ready": QColor("#65A8FF"),
            "Connecting": QColor("#85A6DC"),
        }
        color = colors.get(self._state, QColor("#4D627F"))
        center = QPointF(92, 92)
        active = self._state in self.ACTIVE_STATES
        pulse = 5.0 * (1.0 + math.sin(self._phase)) if active else 0.0
        painter.setPen(Qt.PenStyle.NoPen)
        for radius, alpha in ((77 + pulse, 22), (64 + pulse * 0.6, 48)):
            glow = QColor(color)
            glow.setAlpha(alpha)
            painter.setBrush(glow)
            painter.drawEllipse(center, radius, radius)
        if self._cinematic:
            # Decorative rings: deliberately NOT a microphone amplitude visualization.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for radius, opacity, width in ((80, 85, 1.8), (68, 115, 1.2)):
                ring = QColor(color)
                ring.setAlpha(opacity)
                painter.setPen(QPen(ring, width))
                painter.drawEllipse(center, radius, radius)
            painter.setPen(QPen(color.lighter(135), 2.2))
            rotation = int(math.degrees(self._phase) * 16)
            painter.drawArc(QRectF(14, 14, 156, 156), rotation, 100 * 16)
            painter.drawArc(QRectF(27, 27, 130, 130), rotation + 180 * 16, 78 * 16)
            painter.setPen(Qt.PenStyle.NoPen)
        gradient = QRadialGradient(center, 50)
        gradient.setColorAt(0, QColor("#F3FCFF"))
        gradient.setColorAt(0.2, color.lighter(170))
        gradient.setColorAt(0.7, color)
        gradient.setColorAt(1, color.darker(155))
        painter.setBrush(gradient)
        painter.drawEllipse(center, 47, 47)
        if self._cinematic:
            halo = QColor("#FFFFFF")
            halo.setAlpha(165 if active else 95)
            painter.setPen(QPen(halo, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 47, 47)
        painter.end()

    def _paint_hologram(self, painter: QPainter) -> None:
        """Vector-only amber sci-fi visual; not a measured audio waveform."""
        state_colors = {
            "Ready": QColor("#F5A843"),
            "Listening": QColor("#FFD06B"),
            "Responding": QColor("#FFBB58"),
            "Connecting": QColor("#C68A52"),
        }
        color = state_colors.get(self._state, QColor("#7A6042"))
        center = QPointF(92, 92)
        active = self._state in self.ACTIVE_STATES
        rotation = math.degrees(self._phase) * 0.65 if active else 0.0
        painter.setPen(Qt.PenStyle.NoPen)
        for radius, alpha in ((85, 15), (74, 25), (61, 32)):
            glow = QColor(color)
            glow.setAlpha(alpha)
            painter.setBrush(glow)
            painter.drawEllipse(center, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Fine concentric rings and partial orbits create the hologram-like
        # feeling without GPU shaders, video files or extra dependencies.
        for radius, opacity, width in (
            (82, 85, 0.55), (75, 125, 0.8), (66, 155, 0.65),
            (56, 180, 1.0), (40, 180, 0.75),
        ):
            ring = QColor(color)
            ring.setAlpha(opacity)
            painter.setPen(QPen(ring, width))
            painter.drawEllipse(center, radius, radius)
        painter.setPen(QPen(color.lighter(120), 1.3))
        painter.drawArc(
            QRectF(14, 14, 156, 156),
            int((rotation + 15) * 16), 92 * 16,
        )
        painter.drawArc(
            QRectF(27, 27, 130, 130),
            int((210 - rotation * 0.8) * 16), 105 * 16,
        )
        painter.drawArc(
            QRectF(38, 38, 108, 108),
            int((rotation + 135) * 16), 140 * 16,
        )
        # Discrete radial circuit ticks, driven by session state rather than
        # the microphone or model output. Timer pauses when the orb is hidden.
        for index in range(64):
            theta = math.radians(index * (360 / 64) + rotation)
            outer = 83 if index % 4 == 0 else 79
            inner = outer - (7 if index % 4 == 0 else 3)
            stroke = QColor(color)
            stroke.setAlpha(165 if index % 4 == 0 else 68)
            painter.setPen(QPen(stroke, 1.1 if index % 4 == 0 else 0.6))
            painter.drawLine(
                QPointF(92 + math.cos(theta) * inner, 92 + math.sin(theta) * inner),
                QPointF(92 + math.cos(theta) * outer, 92 + math.sin(theta) * outer),
            )
        painter.setPen(Qt.PenStyle.NoPen)
        gradient = QRadialGradient(center, 38)
        gradient.setColorAt(0, QColor("#FFF3CA"))
        gradient.setColorAt(0.2, color.lighter(155))
        gradient.setColorAt(0.62, color)
        gradient.setColorAt(1, QColor("#583014"))
        painter.setBrush(gradient)
        painter.drawEllipse(center, 33, 33)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color.lighter(145), 1))
        painter.drawEllipse(center, 33, 33)


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
        self._home_academic_snapshot: AcademicSnapshot | None = None
        self._scheduler_stop_requested = False
        self._scheduler_had_error = False
        self._closing = False
        self._quitting = False
        self._tray: QSystemTrayIcon | None = None
        self._state = "Disconnected"
        self._last_recovery: str | None = None
        self._draft: dict[str, str] | None = None
        self._voice_panel: str | None = None
        self._focus_subtitle_speaker: str | None = None
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
        self.sidebar = sidebar
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
        self.top_bar = top
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
        self._navigate("Voice")
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

    @staticmethod
    def _home_summary_row(title: str, detail: str, *, tag: str = "") -> QFrame:
        """Presentation only. All source text stays plain and unclickable."""
        card = QFrame()
        card.setObjectName("summaryRow")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(5)
        if tag:
            label = QLabel(tag)
            label.setObjectName("summaryBadge")
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        heading = QLabel(title)
        heading.setObjectName("itemTitle")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        info = QLabel(detail)
        info.setObjectName("subheading")
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)
        return card

    @staticmethod
    def _clear_home_rows(rows: QVBoxLayout) -> None:
        while rows.count():
            item = rows.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

    def _build_home_page(self) -> None:
        page = self._new_page()
        page.setSpacing(15)
        banner = self._panel()
        banner.setObjectName("hero")
        hero = QHBoxLayout(banner)
        hero.setContentsMargins(19, 13, 21, 13)
        hero.setSpacing(17)
        greeting = QVBoxLayout()
        greeting.setSpacing(7)
        eyebrow = QLabel("YOUR PERSONAL ASSISTANT")
        eyebrow.setObjectName("eyebrow")
        greeting.addWidget(eyebrow)
        self.home_greeting = QLabel("Welcome back, Tobias")
        self.home_greeting.setObjectName("heading")
        greeting.addWidget(self.home_greeting)
        greeting.addWidget(self._plain_label(
            "Your day, coursework and reminders — with FRIDAY ready when you are."
        ))
        hero.addLayout(greeting, 1)
        page.addWidget(banner)

        voice_card = self._panel()
        voice_card.setObjectName("homeVoiceHero")
        voice = QHBoxLayout(voice_card)
        voice.setContentsMargins(18, 14, 20, 14)
        voice.setSpacing(18)
        self.home_orb = VoiceOrb(diameter=140)
        voice.addWidget(self.home_orb, alignment=Qt.AlignmentFlag.AlignVCenter)
        voice_copy = QVBoxLayout()
        voice_copy.setSpacing(9)
        self.home_voice_title = QLabel("Ready when you are")
        self.home_voice_title.setObjectName("pageTitle")
        voice_copy.addWidget(self.home_voice_title)
        self.home_voice_state = self._plain_label("Voice · Disconnected")
        voice_copy.addWidget(self.home_voice_state)
        voice_copy.addWidget(self._plain_label(
            "Click to open Voice, then connect and use the existing microphone controls."
        ))
        launch = self._open_page_button("Open cinematic Voice →", "Voice")
        launch.setObjectName("primary")
        voice_copy.addWidget(launch, alignment=Qt.AlignmentFlag.AlignLeft)
        voice.addLayout(voice_copy, 1)
        page.addWidget(voice_card)

        self.home_columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.home_columns.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        academic = self._panel()
        academic_layout = QVBoxLayout(academic)
        academic_layout.setContentsMargins(17, 15, 17, 15)
        academic_layout.setSpacing(10)
        academic_layout.addLayout(self._heading(
            "Brightspace · upcoming",
            subtitle="Local snapshot · read-only · published calendar entries only",
        ))
        self.home_academic_status = self._plain_label("Brightspace: not synced")
        academic_layout.addWidget(self.home_academic_status)
        self.home_academic_items = self._plain_label("Sync your feed on the Academic page.")
        academic_layout.addWidget(self.home_academic_items)
        self.home_academic_rows = QVBoxLayout()
        self.home_academic_rows.setSpacing(7)
        academic_layout.addLayout(self.home_academic_rows)
        self.home_academic_note = self._plain_label(
            "The feed can omit assignments. Event titles labeled Due are not "
            "independently verified submission deadlines."
        )
        self.home_academic_note.setObjectName("hint")
        academic_layout.addWidget(self.home_academic_note)
        academic_buttons = QHBoxLayout()
        academic_buttons.addWidget(self._open_page_button("View Academic", "Academic"))
        self.home_sync_button = QPushButton("Sync now")
        self.home_sync_button.clicked.connect(self._home_sync_academic)
        academic_buttons.addWidget(self.home_sync_button)
        academic_layout.addLayout(academic_buttons)
        left.addWidget(academic)

        scheduler = self._panel()
        scheduler_layout = QVBoxLayout(scheduler)
        scheduler_layout.setContentsMargins(17, 15, 17, 15)
        scheduler_layout.setSpacing(9)
        scheduler_layout.addLayout(self._heading(
            "Scheduler / Google Calendar",
            subtitle="Local reminders · optional separate Google Calendar sync",
        ))
        self.home_scheduler_status = self._plain_label("Calendar: checking worker…")
        scheduler_layout.addWidget(self.home_scheduler_status)
        self.home_scheduler_note = self._plain_label(
            "Scheduler operation is independent of Gemini. Google sync remains opt-in."
        )
        self.home_scheduler_note.setObjectName("hint")
        scheduler_layout.addWidget(self.home_scheduler_note)
        scheduler_layout.addWidget(self._open_page_button("View Calendar", "Calendar"))
        left.addWidget(scheduler)
        self.home_columns.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(14)
        conversation = self._panel()
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(17, 15, 17, 15)
        conversation_layout.setSpacing(9)
        conversation_layout.addLayout(self._heading(
            "Current conversation",
            subtitle="Current session preview only · no persisted conversation memory",
        ))
        self.home_recent = self._plain_label("Connect to start a conversation.")
        conversation_layout.addWidget(self.home_recent)
        conversation_layout.addWidget(self._open_page_button("Open transcript", "Voice"))
        right.addWidget(conversation)

        reminders = self._panel()
        reminders_layout = QVBoxLayout(reminders)
        reminders_layout.setContentsMargins(17, 15, 17, 15)
        reminders_layout.setSpacing(9)
        reminders_layout.addLayout(self._heading(
            "Upcoming reminders",
            subtitle="Only future pending items from your local reminders",
        ))
        self.home_reminder_status = self._plain_label("Checking local reminders…")
        reminders_layout.addWidget(self.home_reminder_status)
        self.home_reminder_items = self._plain_label("No pending reminders.")
        reminders_layout.addWidget(self.home_reminder_items)
        self.home_reminder_rows = QVBoxLayout()
        self.home_reminder_rows.setSpacing(7)
        reminders_layout.addLayout(self.home_reminder_rows)
        reminders_layout.addWidget(self._plain_label(
            "Approval of voice drafts stays on the Voice page."
        ))
        reminders_layout.addWidget(self._open_page_button("View Reminders", "Reminders"))
        right.addWidget(reminders)
        self.home_columns.addLayout(right, 2)
        page.addLayout(self.home_columns)

        quick = self._panel()
        quick_layout = QVBoxLayout(quick)
        quick_layout.setContentsMargins(17, 14, 17, 14)
        quick_layout.addLayout(self._heading("Quick navigation"))
        actions = QHBoxLayout()
        actions.addWidget(self._open_page_button("Voice", "Voice"))
        actions.addWidget(self._open_page_button("Academic", "Academic"))
        actions.addWidget(self._open_page_button("Reminders", "Reminders"))
        actions.addWidget(self._open_page_button("Calendar", "Calendar"))
        quick_layout.addLayout(actions)
        page.addWidget(quick)
        page.addStretch(1)
        self._adapt_home_layout(self.width())

    def _home_sync_academic(self) -> None:
        """Reuse the canonical manual sync and show its status on the Academic page."""
        if self._academic_sync is not None or self._quitting or self._closing:
            return
        self._navigate("Academic")
        self._sync_academic()

    def _adapt_home_layout(self, width: int) -> None:
        if not hasattr(self, "home_columns"):
            return
        compact = width < 1120
        self.home_columns.setDirection(
            QBoxLayout.Direction.TopToBottom
            if compact else QBoxLayout.Direction.LeftToRight
        )
        self.home_columns.setStretch(0, 0 if compact else 3)
        self.home_columns.setStretch(1, 0 if compact else 2)
        self.top_scheduler_status.setVisible(not compact)
        self.top_academic_status.setVisible(not compact)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._adapt_home_layout(event.size().width())
        self._adapt_voice_layout(event.size().width())

    def _build_voice_page(self, reminders: bool, web: bool) -> None:
        """Cinematic presentation of the existing click-to-talk voice session."""
        page = self._new_page()
        page.setSpacing(13)
        ribbon = self._panel()
        ribbon.setObjectName("voiceRibbon")
        ribbon_layout = QHBoxLayout(ribbon)
        ribbon_layout.setContentsMargins(16, 11, 16, 11)
        ribbon_layout.addWidget(self._plain_label(
            "LIVE VOICE · manual microphone · no wake word"
        ), 1)
        self.voice_state_badge = QLabel("Disconnected")
        self.voice_state_badge.setObjectName("voiceStateBadge")
        self.voice_state_badge.setTextFormat(Qt.TextFormat.PlainText)
        ribbon_layout.addWidget(self.voice_state_badge)
        page.addWidget(ribbon)

        self.voice_columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.voice_columns.setSpacing(14)
        stage = self._panel()
        stage.setObjectName("voiceStage")
        stage.setMinimumWidth(290)
        voice = QVBoxLayout(stage)
        voice.setContentsMargins(22, 21, 22, 19)
        voice.setSpacing(12)
        eyebrow = QLabel("FRIDAY · VOICE")
        eyebrow.setObjectName("eyebrow")
        voice.addWidget(eyebrow, alignment=Qt.AlignmentFlag.AlignHCenter)
        voice.addStretch(1)
        self.orb = VoiceOrb(diameter=260, cinematic=True)
        voice.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.voice_title = QLabel("FRIDAY is offline")
        self.voice_title.setObjectName("voiceHeadline")
        self.voice_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        voice.addWidget(self.voice_title)
        self.voice_hint = self._plain_label(
            "Connect first. Microphone is off until you click."
        )
        self.voice_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        voice.addWidget(self.voice_hint)
        self.voice_activity = self._plain_label(
            "Microphone is off until you press Start talking."
        )
        self.voice_activity.setObjectName("voiceActivity")
        self.voice_activity.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        voice.addWidget(self.voice_activity)
        self.mic_button = QPushButton("Start talking")
        self.mic_button.setObjectName("primary")
        self.mic_button.setMinimumHeight(55)
        self.mic_button.setAccessibleName("Start or stop microphone recording")
        self.mic_button.clicked.connect(self._toggle_microphone)
        voice.addWidget(self.mic_button)
        voice.addWidget(self._plain_label(
            "Connect or disconnect from the top bar. Recording only starts when you click."
        ))
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
        self.voice_columns.addWidget(stage, 5)

        right = QVBoxLayout()
        right.setSpacing(13)
        transcript_panel = self._panel()
        transcript_layout = QVBoxLayout(transcript_panel)
        transcript_layout.setContentsMargins(15, 16, 15, 15)
        transcript_layout.setSpacing(9)
        transcript_layout.addLayout(self._heading(
            "LIVE CONVERSATION",
            subtitle="Visual transcript · window-only · untrusted text stays plain",
        ))
        self.voice_bubble_scroll = QScrollArea()
        self.voice_bubble_scroll.setObjectName("bubbleViewport")
        self.voice_bubble_scroll.setWidgetResizable(True)
        self.voice_bubble_scroll.setMinimumHeight(290)
        bubble_canvas = QWidget()
        bubble_canvas.setObjectName("bubbleCanvas")
        self.voice_bubble_layout = QVBoxLayout(bubble_canvas)
        self.voice_bubble_layout.setContentsMargins(8, 8, 8, 8)
        self.voice_bubble_layout.setSpacing(9)
        self.voice_bubble_empty = self._plain_label(
            "Connect and talk to FRIDAY. The conversation will appear here."
        )
        self.voice_bubble_layout.addWidget(self.voice_bubble_empty)
        self.voice_bubble_layout.addStretch(1)
        self.voice_bubble_scroll.setWidget(bubble_canvas)
        transcript_layout.addWidget(self.voice_bubble_scroll, 1)
        self._voice_bubble_entries: list[tuple[str, QLabel, QFrame]] = []
        self.full_transcript_button = QPushButton("Show full text transcript")
        self.full_transcript_button.setCheckable(True)
        self.full_transcript_button.toggled.connect(self._toggle_full_transcript)
        transcript_layout.addWidget(self.full_transcript_button)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.document().setMaximumBlockCount(250)
        self.transcript.setPlaceholderText("Your conversation appears here while connected.")
        self.transcript.setMinimumHeight(130)
        self.transcript.hide()
        transcript_layout.addWidget(self.transcript)
        self.voice_columns.addLayout(right, 6)
        right.addWidget(transcript_panel, 3)

        glance = self._panel()
        glance_layout = QVBoxLayout(glance)
        glance_layout.setContentsMargins(15, 13, 15, 13)
        glance_layout.setSpacing(8)
        glance_layout.addLayout(self._heading(
            "TODAY AT A GLANCE",
            subtitle="Local snapshots only · no background model request",
        ))
        self.voice_context_academic = self._plain_label("Brightspace: not synced")
        self.voice_context_reminders = self._plain_label("Reminders: checking local data")
        self.voice_context_scheduler = self._plain_label("Scheduler: checking worker")
        glance_layout.addWidget(self.voice_context_academic)
        glance_layout.addWidget(self.voice_context_reminders)
        glance_layout.addWidget(self.voice_context_scheduler)
        context_actions = QHBoxLayout()
        context_actions.addWidget(self._open_page_button("Academic", "Academic"))
        context_actions.addWidget(self._open_page_button("Reminders", "Reminders"))
        glance_layout.addLayout(context_actions)
        right.addWidget(glance, 1)
        page.addLayout(self.voice_columns, 1)
        page.addWidget(self._plain_label(
            "Try asking: \"What's due today?\" or \"List my reminders.\" "
            "These are examples, not auto-sent commands."
        ))
        self._adapt_voice_layout(self.width())

    def _toggle_full_transcript(self, checked: bool) -> None:
        self.transcript.setVisible(checked)
        self.full_transcript_button.setText(
            "Hide full text transcript" if checked else "Show full text transcript"
        )

    def _refresh_voice_context(self) -> None:
        """Read only in-memory UI summaries; never call a model or fetch Brightspace."""
        self.voice_context_academic.setText(self.home_academic_status.text())
        self.voice_context_reminders.setText(self.home_reminder_status.text())
        self.voice_context_scheduler.setText(self.home_scheduler_status.text())

    def _append_voice_bubble(self, speaker: str, content: str) -> None:
        """Bounded in-memory, plain-text rendering; canonical raw text remains below."""
        if not content:
            return
        if speaker == "FRIDAY" and self._voice_bubble_entries:
            previous_speaker, label, _frame = self._voice_bubble_entries[-1]
            if previous_speaker == "FRIDAY" and len(label.text()) < 900:
                label.setText((label.text() + " " + content).strip()[:1200])
                self.voice_bubble_scroll.verticalScrollBar().setValue(
                    self.voice_bubble_scroll.verticalScrollBar().maximum()
                )
                return
        if len(self._voice_bubble_entries) >= 36:
            _old_speaker, _old_label, old_frame = self._voice_bubble_entries.pop(0)
            self.voice_bubble_layout.removeWidget(old_frame)
            old_frame.deleteLater()
        self.voice_bubble_empty.hide()
        card = QFrame()
        card.setObjectName(
            "userBubble" if speaker == "You"
            else "assistantBubble" if speaker == "FRIDAY" else "systemBubble"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(4)
        role = QLabel(speaker)
        role.setObjectName("bubbleRole")
        role.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(role)
        label = self._plain_label(content[:1200])
        label.setObjectName("bubbleText")
        layout.addWidget(label)
        self.voice_bubble_layout.insertWidget(
            self.voice_bubble_layout.count() - 1, card
        )
        self._voice_bubble_entries.append((speaker, label, card))
        QTimer.singleShot(
            0,
            lambda: self.voice_bubble_scroll.verticalScrollBar().setValue(
                self.voice_bubble_scroll.verticalScrollBar().maximum()
            ),
        )

    def _adapt_voice_layout(self, width: int) -> None:
        if not hasattr(self, "voice_columns"):
            return
        self.voice_columns.setDirection(
            QBoxLayout.Direction.TopToBottom
            if width < 1120 else QBoxLayout.Direction.LeftToRight
        )
        self.voice_columns.setStretch(0, 0 if width < 1120 else 5)
        self.voice_columns.setStretch(1, 0 if width < 1120 else 6)

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
        local = datetime.now().astimezone()
        self.clock_status.setText(local.strftime("%a %b %d · %I:%M %p"))
        greeting = (
            "Good morning" if local.hour < 12
            else "Good afternoon" if local.hour < 17 else "Good evening"
        )
        self.home_greeting.setText(f"{greeting}, Tobias")

    def _refresh_home_academic(self) -> None:
        """Render only the already-read local snapshot, never a feed URL or network."""
        self.home_academic_status.setText(self.academic_status.text())
        self._clear_home_rows(self.home_academic_rows)
        snapshot = self._home_academic_snapshot
        if snapshot is None:
            self.home_academic_items.setText("Local academic cache unavailable.")
            self.home_academic_items.show()
            return
        if not snapshot.last_success:
            self.home_academic_items.setText("No synced Brightspace calendar yet.")
            self.home_academic_items.show()
            return
        if not snapshot.items:
            self.home_academic_items.setText("No upcoming published calendar items.")
            self.home_academic_items.show()
            return
        self.home_academic_items.setText(
            f"{len(snapshot.items)} upcoming published calendar item(s) in the next 7 days."
        )
        self.home_academic_items.show()
        for item in snapshot.items[:3]:
            label = (
                "Due (task)" if item.explicit_due
                else "Brightspace-labeled due (event)" if source_labeled_due(item)
                else "Scheduled"
            )
            if item.recurring:
                label += " · recurring series (not expanded)"
            self.home_academic_rows.addWidget(
                self._home_summary_row(item.title, display_time(item), tag=label)
            )

    def _refresh_home_reminders(self) -> None:
        """Read local future reminders without initializing or migrating SQLite."""
        try:
            count, records = read_pending_reminder_preview(limit=3)
        except (OSError, sqlite3.Error, ValueError):
            self.home_reminder_status.setText("Local reminders unavailable.")
            self.home_reminder_items.setText("See Reminders for the current voice list.")
            self.home_reminder_items.show()
            self._clear_home_rows(self.home_reminder_rows)
            return
        self.home_reminder_status.setText(f"{count} pending reminder(s) in local storage.")
        self._clear_home_rows(self.home_reminder_rows)
        self.home_reminder_items.setText(
            "No pending reminders." if not count
            else f"Showing {len(records)} of {count} upcoming reminder(s)."
        )
        self.home_reminder_items.show()
        for text, due_at in records:
            when = datetime.fromtimestamp(due_at).astimezone()
            self.home_reminder_rows.addWidget(self._home_summary_row(
                text, when.strftime("%a, %b %d · %I:%M %p"), tag="Local reminder"
            ))
        self._refresh_voice_context()

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
            self._home_academic_snapshot = None
            self.academic_voice_option.setEnabled(False)
            self._refresh_academic_overview()
            return
        self._academic_cache_ready = bool(last)
        self._home_academic_snapshot = snapshot
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
        self._refresh_voice_context()

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
            self.home_sync_button,
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
        self._refresh_home_reminders()
        self._refresh_voice_context()
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
            self._append_voice_bubble(label, text)

    def _set_state(self, state: str) -> None:
        self._state = state
        self.status.setText(state)
        self.home_voice_state.setText(f"Voice · {state}")
        self.home_voice_title.setText(
            "Ready when you are" if state == "Ready"
            else "Listening…" if state == "Listening"
            else "FRIDAY is responding" if state == "Responding"
            else "Connect to start talking" if state == "Disconnected"
            else state
        )
        self.home_orb.set_state(state)
        self.orb.set_state(state)
        self.voice_state_badge.setText(state)
        phase = (
            state if state in {"Listening", "Responding", "Ready"}
            else "Inactive"
        )
        self.voice_state_badge.setProperty("phase", phase)
        self.voice_state_badge.style().unpolish(self.voice_state_badge)
        self.voice_state_badge.style().polish(self.voice_state_badge)
        activities = {
            "Ready": "Microphone off · click Start talking when ready.",
            "Listening": "Recording is active · click Stop recording to finish.",
            "Responding": "FRIDAY is replying · microphone recording is off.",
            "Connecting": "Establishing your voice connection…",
            "Disconnecting": "Closing the voice session…",
        }
        self.voice_activity.setText(
            activities.get(state, "Microphone off · no automatic reconnection.")
        )
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
            self._refresh_home_reminders()
            self.home_reminder_status.setText(
                f"{len(value)} pending reminder(s) reported by the current voice session."
            )
            self._refresh_voice_context()
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
            self._refresh_home_reminders()

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
