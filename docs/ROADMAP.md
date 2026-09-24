# FRIDAY roadmap

## Completed target: v0.1 Harness Foundation

- Minimal Python package, configuration, CLI.
- Provider protocol and deterministic fake provider.
- Gemini Live adapter for text-triggered diagnostic and normalized audio/transcription events.
- Session state machine, error handling, idempotent shutdown.
- Unit tests without external API usage.

## v0.2 Voice Vertical Slice (implemented; Windows hardware acceptance pending)

- sounddevice/PortAudio device abstraction; 16 kHz mono PCM input and 24 kHz PCM output.
- Enter-to-talk/Enter-to-stop toggle, bounded microphone sender and bounded playback buffer.
- Playback flush on Gemini interruption or new turn; clean device shutdown on errors.
- `talk` command streams a microphone-to-Gemini-to-speaker conversation with transcript display.
- Offline tests with fake audio source/sink pass; user hardware and live integration remain to validate.

## Later

- Wake word, local speech pipeline (STT -> LLM -> TTS), persistent SQLite memory.
- Typed tool registry with human approval, per-action policy and audit logs.
- Session resumption and cost/usage telemetry.
- Local desktop interface and hybrid routing only after the core audio path is stable.
