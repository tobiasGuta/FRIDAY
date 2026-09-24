"""Harness demo, text-to-audio diagnostic, and interactive voice session."""

import argparse
import asyncio
import logging
import wave
from dataclasses import dataclass
from pathlib import Path

from friday import __version__
from friday.audio.devices import AudioDeviceError, Microphone, Speaker, list_audio_devices
from friday.audio.turns import VoiceTurns
from friday.config import Settings
from friday.core.events import EventKind, SessionState, VoiceEvent
from friday.core.session import SessionError, SessionManager
from friday.providers.fake import FakeVoiceProvider
from friday.providers.gemini_live import GeminiLiveProvider
from friday.tools.builtins import build_builtin_registry
from friday.tools.local_clock import read_local_clock
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
) -> bool:
    """Single consumer: play PCM and print transcripts; return False on provider failure."""
    async for event in manager.events():
        if event.kind is EventKind.AUDIO:
            speaker.enqueue(event.audio or b"", sample_rate=event.sample_rate or 0)
            if diagnostics is not None:
                diagnostics.assistant_audio_bytes += len(event.audio or b"")
        elif event.kind is EventKind.TRANSCRIPT:
            if diagnostics is not None and event.speaker == "user":
                diagnostics.user_transcript_chunks += 1
            _display(event)
        elif event.kind is EventKind.INTERRUPTED:
            speaker.flush()
            _display(event)
        else:
            _display(event)
        if event.kind is EventKind.TURN_COMPLETE:
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
) -> str:
    """Block new turns while permitting /quit and bounding an unresponsive provider."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        command_task = asyncio.create_task(commands.next())
        finish_task = asyncio.create_task(turn_finished.wait())
        try:
            done, _ = await asyncio.wait(
                {command_task, finish_task, receiver},
                timeout=max(0.0, deadline - loop.time()),
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
            if not done:
                return "timeout"
            print("FRIDAY is responding. Wait for her to finish or type /quit.")
        finally:
            for task in (command_task, finish_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(command_task, finish_task, return_exceptions=True)


async def _talk(args: argparse.Namespace, settings: Settings) -> int:
    settings.require_gemini_key()
    # Only the explicit talk command opens a microphone. The text demo is unaffected.
    loop = asyncio.get_running_loop()
    mic = Microphone(
        loop=loop, sample_rate=settings.input_sample_rate, device=args.input_device
    )
    speaker = Speaker(sample_rate=settings.output_sample_rate, device=args.output_device)
    manager = SessionManager(
        GeminiLiveProvider(settings, manual_activity=True, enable_local_clock=True),
        queue_size=settings.event_queue_size
    )
    commands = TerminalCommands()
    turns = VoiceTurns(manager, mic, speaker)
    receiver: asyncio.Task[bool] | None = None
    turn_finished = asyncio.Event()
    diagnostics = VoiceTurnDiagnostics()
    try:
        # If hardware initialization fails, do not open the paid provider connection.
        speaker.start()
        await manager.start()
        receiver = asyncio.create_task(
            _voice_events(manager, speaker, turn_finished, diagnostics),
            name="friday-speaker-events"
        )
        commands.start()
        print("FRIDAY is connected. Use headphones to avoid microphone/speaker feedback.")
        print("Press ENTER to start speaking, then ENTER again to stop. Type /quit to exit.")
        async with asyncio.timeout(args.max_seconds or settings.max_session_seconds):
            while True:
                if turns.awaiting_response:
                    result = await _await_voice_response(
                        commands, receiver, turn_finished, speaker
                    )
                    if result == "quit":
                        break
                    if result == "timeout":
                        print(
                            "FRIDAY response timed out (30s): "
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
        if mic.dropped_chunks:
            print(f"Microphone overload: {mic.dropped_chunks} chunks dropped")
        if speaker.dropped_bytes:
            print(f"Speaker overload: {speaker.dropped_bytes} bytes dropped")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="friday", description="FRIDAY voice assistant v0.2")
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
    talk = sub.add_parser("talk", help="Live microphone -> Gemini -> speaker conversation")
    talk.add_argument("--input-device", type=int, help="Optional PortAudio input device index")
    talk.add_argument("--output-device", type=int, help="Optional PortAudio output device index")
    talk.add_argument("--max-seconds", type=int, help="Override session duration limit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level), format="%(levelname)s %(message)s"
    )
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
        if args.command == "tools":
            for name, policy in build_builtin_registry(enable_local_clock=True).available_tools():
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
