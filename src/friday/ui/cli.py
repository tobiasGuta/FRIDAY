"""Harness demo, text-to-audio diagnostic, and interactive voice session."""

import argparse
import asyncio
import logging
import tempfile
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from friday import __version__
from friday.audio.devices import AudioDeviceError, Microphone, Speaker, list_audio_devices
from friday.audio.turns import VoiceTurns
from friday.calendar_sync import (
    CalendarStore,
    CalendarSyncError,
    connect_google,
    google_service,
    initialize_calendar,
    sync_calendar,
    token_path,
)
from friday.config import Settings
from friday.core.events import EventKind, SearchSource, SessionState, VoiceEvent
from friday.core.session import SessionError, SessionManager
from friday.providers.fake import FakeVoiceProvider
from friday.providers.gemini_live import GeminiLiveProvider
from friday.schedule import ScheduleStore, run_worker, work_once
from friday.tools.builtins import build_builtin_registry
from friday.tools.local_clock import read_local_clock
from friday.tools.search_grounding import SearchPreview
from friday.tools.weather import WeatherArguments, WeatherService
from friday.tools.web_search import WebSearchService
from friday.ui.terminal import TerminalCommands


def _display(event: VoiceEvent) -> None:
    if event.kind is EventKind.TRANSCRIPT:
        print(f"{event.speaker or 'unknown'}: {event.text}")
    elif event.kind is EventKind.ERROR:
        print(f"ERROR: {event.text}")
    elif event.kind is EventKind.NOTICE:
        print(f"NOTICE: {event.text}")
    elif event.kind is EventKind.INTERRUPTED:
        print("FRIDAY was interrupted")


async def _demo(args: argparse.Namespace, settings: Settings) -> int:
    manager = SessionManager(FakeVoiceProvider(), queue_size=settings.event_queue_size)
    await manager.start()
    try:
        if args.once:
            await manager.send_text(args.once)
            async with asyncio.timeout(2):
                async for event in manager.events():
                    _display(event)
                    if event.kind is EventKind.TURN_COMPLETE:
                        break
            return 0
        print("FRIDAY fake-provider demo. Type /quit to exit; no API key is needed.")
        while True:
            text = await asyncio.to_thread(input, "You> ")
            if text.strip().lower() in {"/quit", "/exit"}:
                break
            if not text.strip():
                continue
            await manager.send_text(text)
            async with asyncio.timeout(2):
                async for event in manager.events():
                    _display(event)
                    if event.kind is EventKind.TURN_COMPLETE:
                        break
        return 0
    finally:
        await manager.close()


async def _live(args: argparse.Namespace, settings: Settings) -> int:
    # Validate before opening any external connection or output file.
    settings.require_gemini_key()
    manager = SessionManager(
        GeminiLiveProvider(settings, enable_local_clock=True), queue_size=settings.event_queue_size
    )
    await manager.start()
    audio_bytes = 0
    output: wave.Wave_write | None = None
    try:
        if args.output:
            path = Path(args.output)
            output = wave.open(str(path), "wb")
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(settings.output_sample_rate)
        await manager.send_text(args.text)
        print("Connected. This text diagnostic saves audio; use talk for speaker playback.")
        async with asyncio.timeout(min(args.timeout, settings.max_session_seconds)):
            async for event in manager.events():
                _display(event)
                if event.kind is EventKind.AUDIO:
                    audio_bytes += len(event.audio or b"")
                    if output is not None:
                        output.writeframes(event.audio or b"")
                if event.kind in {EventKind.ERROR, EventKind.TURN_COMPLETE}:
                    break
        print(f"Received {audio_bytes} PCM audio bytes")
        if args.output:
            print(f"WAV output: {args.output}")
        return 0 if manager.state is SessionState.READY else 1
    finally:
        if output is not None:
            output.close()
        await manager.close()


@dataclass(slots=True)
class VoiceTurnDiagnostics:
    user_transcript_chunks: int = 0
    assistant_audio_bytes: int = 0
    completions: int = 0
    interruptions: int = 0

    def reset(self) -> None:
        self.user_transcript_chunks = 0
        self.assistant_audio_bytes = 0
        self.completions = 0
        self.interruptions = 0


async def _voice_events(
    manager: SessionManager,
    speaker: Speaker,
    turn_finished: asyncio.Event | None = None,
    diagnostics: VoiceTurnDiagnostics | None = None,
    search_preview: SearchPreview | None = None,
) -> bool:
    """Single consumer: play PCM, display supplied sources, and track turn state."""
    sources: dict[str, SearchSource] = {}
    suggestions: str | None = None
    answer_parts: list[str] = []
    async for event in manager.events():
        if event.kind is EventKind.AUDIO:
            await speaker.enqueue_wait(event.audio or b"", sample_rate=event.sample_rate or 0)
            if diagnostics is not None:
                diagnostics.assistant_audio_bytes += len(event.audio or b"")
        elif event.kind is EventKind.GROUNDING:
            for source in event.sources:
                if len(sources) < 10:
                    sources.setdefault(source.url, source)
            if event.search_suggestions_html:
                suggestions = event.search_suggestions_html
        elif event.kind is EventKind.TRANSCRIPT:
            if diagnostics is not None and event.speaker == "user":
                diagnostics.user_transcript_chunks += 1
            if event.speaker == "assistant" and event.text:
                answer_parts.append(event.text)
            _display(event)
        elif event.kind is EventKind.INTERRUPTED:
            speaker.flush()
            sources.clear()
            suggestions = None
            answer_parts.clear()
            _display(event)
        else:
            _display(event)
        if event.kind is EventKind.TURN_COMPLETE:
            if sources or suggestions:
                print("Web search sources returned:")
                if sources:
                    for index, source in enumerate(sources.values(), 1):
                        print(f"  [{index}] {source.title} — {source.url}")
                else:
                    print("  No usable source URLs were provided for this answer.")
                if suggestions and search_preview is not None:
                    try:
                        opened = await asyncio.to_thread(
                            search_preview.show,
                            "".join(answer_parts),
                            tuple(sources.values()),
                            suggestions,
                        )
                    except OSError:
                        opened = False
                    print(
                        "Google Search suggestions opened in your browser."
                        if opened else
                        "WARNING: Could not open Google Search suggestions in a browser."
                    )
            sources.clear()
            suggestions = None
            answer_parts.clear()
            print("FRIDAY turn complete")
        if event.kind in {EventKind.TURN_COMPLETE, EventKind.INTERRUPTED}:
            if diagnostics is not None:
                if event.kind is EventKind.TURN_COMPLETE:
                    diagnostics.completions += 1
                else:
                    diagnostics.interruptions += 1
            if turn_finished is not None:
                turn_finished.set()
        if event.kind is EventKind.ERROR:
            return False
        if event.kind is EventKind.STATE and event.state is SessionState.CLOSED:
            return True
    return False


async def _await_voice_response(
    commands: TerminalCommands,
    receiver: asyncio.Task[bool],
    turn_finished: asyncio.Event,
    speaker: Speaker,
    *,
    timeout: float = 30.0,
    diagnostics: VoiceTurnDiagnostics | None = None,
) -> str:
    """Block new turns until completion; time out only after playback goes idle.

    Progress from provider events or actual speaker playback renews the deadline.
    Extra Enter presses do not count as progress; /quit remains available.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    def progress_snapshot() -> tuple[int, ...]:
        if diagnostics is None:
            return ()
        return (
            diagnostics.user_transcript_chunks,
            diagnostics.assistant_audio_bytes,
            diagnostics.completions,
            diagnostics.interruptions,
            speaker.played_bytes,
        )

    previous_progress = progress_snapshot()
    while True:
        command_task = asyncio.create_task(commands.next())
        finish_task = asyncio.create_task(turn_finished.wait())
        try:
            done, _ = await asyncio.wait(
                {command_task, finish_task, receiver},
                timeout=min(0.2, max(0.0, deadline - loop.time())),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receiver in done:
                return "closed" if receiver.result() else "failed"
            if command_task in done:
                command = command_task.result().strip().lower()
                if command in {"/quit", "/exit"}:
                    return "quit"
            if finish_task in done:
                if not await speaker.wait_until_drained(timeout=5.0):
                    speaker.flush()
                    print("WARNING: speaker buffer did not drain; discarded pending audio")
                commands.discard_pending_empty()
                return "ready"
            current_progress = progress_snapshot()
            if current_progress != previous_progress:
                previous_progress = current_progress
                deadline = loop.time() + timeout
            if not done and loop.time() >= deadline:
                return "timeout"
            if done:
                print("FRIDAY is responding. Wait for her to finish or type /quit.")
        finally:
            for task in (command_task, finish_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(command_task, finish_task, return_exceptions=True)


async def _web_check(args: argparse.Namespace, settings: Settings) -> int:
    """Diagnose Live setup with no microphone, speakers, prompt, or search query."""
    settings.require_gemini_key()
    mode = "Delegated search + local clock" if args.with_clock else "Delegated search only"
    print(f"Checking Gemini Live function setup: {mode} (no microphone or search query).")
    manager = SessionManager(
        GeminiLiveProvider(
            settings, manual_activity=True, enable_local_clock=args.with_clock,
            enable_web_search=True,
        ),
        queue_size=settings.event_queue_size,
    )
    try:
        await manager.start()
        print(
            f"Gemini accepted the {mode} connection configuration. "
            "This does not run a search query."
        )
        return 0
    finally:
        await manager.close()


async def _talk(args: argparse.Namespace, settings: Settings) -> int:
    settings.require_gemini_key()
    if args.web and settings.search_backend == "tavily":
        settings.require_tavily_key()
    # Only the explicit talk command opens a microphone. The text demo is unaffected.
    loop = asyncio.get_running_loop()
    mic = Microphone(
        loop=loop, sample_rate=settings.input_sample_rate, device=args.input_device
    )
    speaker = Speaker(sample_rate=settings.output_sample_rate, device=args.output_device)
    manager = SessionManager(
        GeminiLiveProvider(
            settings,
            manual_activity=True,
            enable_local_clock=True,
            enable_web_search=args.web,
            enable_weather=True,
        ),
        queue_size=settings.event_queue_size,
    )
    commands = TerminalCommands()
    turns = VoiceTurns(manager, mic, speaker)
    receiver: asyncio.Task[bool] | None = None
    turn_finished = asyncio.Event()
    diagnostics = VoiceTurnDiagnostics()
    preview_directory = (
        tempfile.TemporaryDirectory(prefix="friday-grounding-") if args.web else None
    )
    search_preview = (
        SearchPreview(Path(preview_directory.name)) if preview_directory is not None else None
    )
    try:
        # If hardware initialization fails, do not open the paid provider connection.
        speaker.start()
        await manager.start()
        receiver = asyncio.create_task(
            _voice_events(
                manager, speaker, turn_finished, diagnostics, search_preview=search_preview
            ),
            name="friday-speaker-events"
        )
        commands.start()
        print("FRIDAY is connected. Use headphones to avoid microphone/speaker feedback.")
        print("Press ENTER to start speaking, then ENTER again to stop. Type /quit to exit.")
        if args.web:
            print(
                f"Web search enabled ({settings.search_backend}); "
                "lookups use separate API credits/quota."
            )
        async with asyncio.timeout(args.max_seconds or settings.max_session_seconds):
            while True:
                if turns.awaiting_response:
                    result = await _await_voice_response(
                        commands, receiver, turn_finished, speaker, diagnostics=diagnostics
                    )
                    if result == "quit":
                        break
                    if result == "timeout":
                        print(
                            "FRIDAY response timed out (30s without audio progress): "
                            f"sent_frames={turns.sent_frames}, sent_bytes={turns.sent_bytes}, "
                            f"user_transcript_chunks={diagnostics.user_transcript_chunks}, "
                            f"assistant_audio_bytes={diagnostics.assistant_audio_bytes}, "
                            f"completed={diagnostics.completions}, "
                            f"interrupted={diagnostics.interruptions}. "
                            "This does not by itself indicate a quota error."
                        )
                        return 1
                    if result in {"failed", "closed"}:
                        return 0 if result == "closed" else 1
                    turns.response_finished()
                    print("FRIDAY is ready. Press Enter to speak.")
                    continue
                command_task = asyncio.create_task(commands.next())
                done, _ = await asyncio.wait(
                    {command_task, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                if receiver in done:
                    command_task.cancel()
                    await asyncio.gather(command_task, return_exceptions=True)
                    return 0 if receiver.result() else 1
                command = command_task.result().strip().lower()
                if command in {"/quit", "/exit"}:
                    break
                if command:
                    print("Unknown command; press Enter to toggle the microphone or type /quit.")
                    continue
                if turns.recording:
                    await turns.stop()
                    print(
                        "Microphone paused. FRIDAY is responding... "
                        f"(sent_frames={turns.sent_frames}, sent_bytes={turns.sent_bytes})"
                    )
                else:
                    # Reset only at the start of a new turn, not after stop: a
                    # fast completion must remain visible to the response waiter.
                    turn_finished.clear()
                    diagnostics.reset()
                    await turns.start()
                    print("Recording... press Enter to stop.")
        return 0
    except TimeoutError:
        print("Session time limit reached. Microphone and connection closing.")
        return 0
    finally:
        commands.close()
        try:
            await turns.close()
        finally:
            if receiver is not None:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            await manager.close()
            speaker.close()
            if preview_directory is not None:
                preview_directory.cleanup()
        if mic.dropped_chunks:
            print(f"Microphone overload: {mic.dropped_chunks} chunks dropped")
        if speaker.dropped_bytes:
            print(f"Speaker overload: {speaker.dropped_bytes} bytes dropped")
        if speaker.status_events:
            print(f"Speaker device status events: {speaker.status_events}")


def _schedule_cli(args: argparse.Namespace) -> int:
    """No network, Gemini, or microphone: a separate process owns delivery."""
    store = ScheduleStore(args.db)
    action = args.schedule_action
    if action == "timer":
        item = store.timer(args.seconds, args.text)
    elif action == "add":
        item = store.reminder(args.at, args.text)
    elif action == "list":
        items = store.list_items(include_history=args.all)
        if not items:
            print("No schedules found.")
        for item in items:
            local_due = datetime.fromtimestamp(item.due_at).astimezone().isoformat(
                timespec="seconds"
            )
            print(f"{item.id} [{item.status}] {item.kind}: {item.text} — {local_due}")
        return 0
    elif action == "cancel":
        if not store.cancel(args.id):
            print("No pending schedule with that ID (it may be processing or completed).")
            return 1
        print("Schedule cancelled.")
        return 0
    elif action == "worker":
        if args.once:
            if args.calendar_sync:
                raise ValueError("--calendar-sync requires the running worker, not --once")
            print(f"Delivered {work_once(store)} due schedule(s).")
            return 0
        callback = None
        if args.calendar_sync:
            calendar = CalendarStore(store)
            if not calendar.calendar_id():
                raise CalendarSyncError("Initialize the FRIDAY calendar before enabling sync.")
            service = google_service()

            def callback() -> tuple[int, int]:
                return sync_calendar(calendar, service)
        try:
            run_worker(store, calendar_sync=callback)
        except KeyboardInterrupt:
            print("FRIDAY scheduler stopped.")
        return 0
    elif action == "calendar":
        calendar = CalendarStore(store)
        if args.calendar_action == "status":
            total, linked = calendar.counts()
            print(f"Google authorization saved: {token_path().is_file()}")
            print(f"FRIDAY calendar initialized: {calendar.calendar_id() is not None}")
            print(f"Local calendar links: {total} ({linked} active)")
            return 0
        if args.calendar_action == "connect":
            connect_google(secrets=args.client_secrets)
            print("Google Calendar authorized locally. No calendar was created yet.")
            return 0
        service = google_service()
        if args.calendar_action == "init":
            _id, created = initialize_calendar(calendar, service)
            print(
                "Dedicated FRIDAY calendar created."
                if created else "Dedicated FRIDAY calendar already initialized."
            )
            return 0
        if args.calendar_action == "sync":
            published, removed = sync_calendar(calendar, service)
            print(f"FRIDAY calendar sync: {published} published, {removed} removed.")
            return 0
        return 2
    else:
        return 2
    local_due = datetime.fromtimestamp(item.due_at).astimezone().isoformat(
        timespec="seconds"
    )
    print(f"Created {item.kind} {item.id}: {item.text} — due {local_due}")
    print("Start 'python -m friday schedule worker' in a separate terminal for alerts.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="friday", description="FRIDAY voice assistant")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Show safe configuration diagnostics")
    sub.add_parser("devices", help="List microphone and speaker devices")
    sub.add_parser("clock", help="Read the computer local clock offline (no API usage)")
    sub.add_parser("tools", help="List enabled tool capabilities offline (no API usage)")
    demo = sub.add_parser("demo", help="Run the no-network fake-provider conversation")
    demo.add_argument("--once", help="Run one fake turn non-interactively")
    live = sub.add_parser("live", help="Opt-in Gemini Live diagnostic (uses your API key)")
    live.add_argument("--text", default="Hello FRIDAY. Introduce yourself in one sentence.")
    live.add_argument("--output", help="Explicitly save generated speech as a 24 kHz WAV")
    live.add_argument("--timeout", type=int, default=45, help="Maximum wait in seconds")
    web_check = sub.add_parser(
        "web-check", help="Check delegated search function setup without microphone or prompt"
    )
    web_check.add_argument(
        "--with-clock", action="store_true",
        help="Also declare FRIDAY's clock, to diagnose combined-tool support",
    )
    web_search = sub.add_parser(
        "web-search", help="Run one explicit search without opening voice devices"
    )
    web_search.add_argument("--query", required=True, help="Public query (3–200 characters)")
    weather = sub.add_parser("weather", help="Check current/tomorrow weather without microphone")
    weather.add_argument("--location", required=True, help="City, state or country (required)")
    weather.add_argument("--day", choices=("today", "tomorrow"), default="today")
    schedule = sub.add_parser("schedule", help="Local persistent timers and reminders")
    schedule.add_argument("--db", type=Path, help="Optional SQLite database path")
    actions = schedule.add_subparsers(dest="schedule_action", required=True)
    timer = actions.add_parser("timer", help="Create a timer")
    timer.add_argument("--seconds", type=int, required=True, help="Duration (1–604800)")
    timer.add_argument("--text", default="Timer finished", help="Notification text")
    add = actions.add_parser("add", help="Add a one-time reminder")
    add.add_argument("--at", required=True, help="ISO date/time with UTC offset")
    add.add_argument("--text", required=True, help="Reminder message")
    listing = actions.add_parser("list", help="List upcoming reminders and timers")
    listing.add_argument("--all", action="store_true", help="Include delivered and cancelled")
    cancel = actions.add_parser("cancel", help="Cancel a pending schedule")
    cancel.add_argument("id", help="ID displayed by 'schedule list'")
    worker = actions.add_parser("worker", help="Deliver alerts independently of voice mode")
    worker.add_argument("--once", action="store_true", help="Process currently due jobs then exit")
    worker.add_argument(
        "--calendar-sync", action="store_true",
        help="Opt in to syncing Google Calendar on startup and every 60 seconds",
    )
    calendar = actions.add_parser("calendar", help="Opt-in Google Calendar synchronization")
    calendar_actions = calendar.add_subparsers(dest="calendar_action", required=True)
    connect = calendar_actions.add_parser("connect", help="Authorize using browser OAuth")
    connect.add_argument(
        "--client-secrets", type=Path, help="Google Desktop OAuth client JSON location",
    )
    calendar_actions.add_parser("init", help="Create or verify dedicated FRIDAY calendar")
    calendar_actions.add_parser("sync", help="Publish pending reminders and cancellations once")
    calendar_actions.add_parser("status", help="Show local calendar sync state")
    talk = sub.add_parser("talk", help="Live microphone -> Gemini -> speaker conversation")
    talk.add_argument("--input-device", type=int, help="Optional PortAudio input device index")
    talk.add_argument("--output-device", type=int, help="Optional PortAudio output device index")
    talk.add_argument("--max-seconds", type=int, help="Override session duration limit")
    talk.add_argument(
        "--web", action="store_true",
        help="Opt in to web search and source links (Tavily by default)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level), format="%(levelname)s %(message)s"
    )
    # HTTP GET URLs contain the user's requested location/coordinates.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "doctor":
        print(f"FRIDAY {__version__}")
        print(f"Provider configured: {settings.provider}")
        print(f"Model: {settings.model}")
        has_key = bool(
            settings.gemini_api_key and settings.gemini_api_key.get_secret_value().strip()
        )
        print(f"Gemini key configured: {has_key}")
        print("Audio devices: available through optional voice dependency (run friday devices)")
        return 0
    try:
        if args.command == "schedule":
            return _schedule_cli(args)
        if args.command == "tools":
            for name, policy in build_builtin_registry(
                enable_local_clock=True, enable_weather=True
            ).available_tools():
                print(f"{name}: {policy.value}")
            return 0
        if args.command == "clock":
            current = read_local_clock()
            print(
                f"Computer local time: {current['time_12h']} "
                f"on {current['weekday']}, {current['date']} "
                f"({current['timezone_name']}, UTC{current['utc_offset']})"
            )
            return 0
        if args.command == "devices":
            print(list_audio_devices())
            return 0
        if args.command == "weather":
            from pydantic import ValidationError

            try:
                request = WeatherArguments.model_validate(
                    {"location": args.location, "day": args.day}, strict=True
                )
            except ValidationError:
                print("FRIDAY: Weather location must contain 3–100 characters.")
                return 1
            result = WeatherService().lookup(request.location, request.day)
            if result.get("status") != "ok":
                if result.get("error") == "ambiguous_location":
                    print("Specify city and state/country. Possible matches:")
                    for option in result["options"]:
                        print(f"  {option}")
                else:
                    print(f"Weather unavailable: {result.get('error', 'unknown_error')}")
                return 1
            print(f"Weather data by Open-Meteo.com: {result['location']} ({result['date']})")
            print(
                f"{result['conditions']}; high {result['high_f']:g}°F, "
                f"low {result['low_f']:g}°F."
            )
            chance = result['precipitation_probability_max_percent']
            print(
                "Maximum precipitation probability: "
                + (f"{chance:g}%." if chance is not None else "not provided.")
            )
            if 'current_temperature_f' in result:
                print(f"Current temperature: {result['current_temperature_f']:g}°F.")
            print(result['source_url'])
            return 0
        if args.command == "web-search":
            from pydantic import ValidationError

            from friday.tools.web_search import SearchArguments

            try:
                query = SearchArguments.model_validate({"query": args.query}, strict=True).query
            except ValidationError:
                print("FRIDAY: Search query must contain 3–200 characters.")
                return 1
            result = WebSearchService(settings).search(query)
            if result.get("status") != "ok":
                print(f"Web search unavailable: {result.get('error', 'unknown_error')}")
                return 1
            print(result["answer"])
            print("Web search sources returned:")
            for source in result["sources"]:
                print(f"  {source['title']} — {source['url']}")
            return 0
        if args.command == "web-check":
            return asyncio.run(_web_check(args, settings))
        if args.command == "talk":
            if args.max_seconds is not None and args.max_seconds < 5:
                raise ValueError("--max-seconds must be at least 5")
            return asyncio.run(_talk(args, settings))
        if args.command == "demo":
            return asyncio.run(_demo(args, settings))
        if args.command == "live":
            if args.timeout < 1:
                raise ValueError("--timeout must be positive")
            return asyncio.run(_live(args, settings))
    except (
        SessionError, AudioDeviceError, ValueError, TimeoutError, RuntimeError, OSError, EOFError
    ) as exc:
        print(f"FRIDAY: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nFRIDAY stopped")
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
