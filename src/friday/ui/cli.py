"""Offline harness demo and opt-in Gemini Live text-to-audio diagnostic."""

import argparse
import asyncio
import logging
import wave
from pathlib import Path

from friday import __version__
from friday.config import Settings
from friday.core.events import EventKind, SessionState, VoiceEvent
from friday.core.session import SessionError, SessionManager
from friday.providers.fake import FakeVoiceProvider
from friday.providers.gemini_live import GeminiLiveProvider


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
    manager = SessionManager(GeminiLiveProvider(settings), queue_size=settings.event_queue_size)
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
        print("Connected. Receiving Gemini audio/transcriptions (speaker playback not yet built).")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="friday", description="FRIDAY harness v0.1")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Show safe configuration diagnostics")
    demo = sub.add_parser("demo", help="Run the no-network fake-provider conversation")
    demo.add_argument("--once", help="Run one fake turn non-interactively")
    live = sub.add_parser("live", help="Opt-in Gemini Live diagnostic (uses your API key)")
    live.add_argument("--text", default="Hello FRIDAY. Introduce yourself in one sentence.")
    live.add_argument("--output", help="Explicitly save generated speech as a 24 kHz WAV")
    live.add_argument("--timeout", type=int, default=45, help="Maximum wait in seconds")
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
        print("Audio devices: not integrated in harness foundation")
        return 0
    try:
        if args.command == "demo":
            return asyncio.run(_demo(args, settings))
        if args.command == "live":
            if args.timeout < 1:
                raise ValueError("--timeout must be positive")
            return asyncio.run(_live(args, settings))
    except (SessionError, ValueError, TimeoutError, RuntimeError, OSError, EOFError) as exc:
        print(f"FRIDAY: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nFRIDAY stopped")
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
