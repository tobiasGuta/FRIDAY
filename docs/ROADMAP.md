# FRIDAY roadmap

## Completed target: v0.1 Harness Foundation

- Minimal Python package, configuration, CLI.
- Provider protocol and deterministic fake provider.
- Gemini Live adapter for text-triggered diagnostic and normalized audio/transcription events.
- Session state machine, error handling, idempotent shutdown.
- Unit tests without external API usage.

## Next: v0.2 Voice Vertical Slice

- PyAudio/sounddevice device abstraction; 16 kHz mono PCM input and 24 kHz PCM output.
- Push-to-talk; bounded microphone sender and dedicated playback queue.
- Playback flush on interruption; close/restart device on failures.
- Real microphone-to-Gemini-to-speaker conversation and transcript display.
- Tests with fake audio source/sink before hardware test.

## Later

- Wake word, local speech pipeline (STT -> LLM -> TTS), persistent SQLite memory.
- Typed tool registry with human approval, per-action policy and audit logs.
- Session resumption and cost/usage telemetry.
- Local desktop interface and hybrid routing only after the core audio path is stable.
