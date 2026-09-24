# FRIDAY roadmap

## Completed target: v0.1 Harness Foundation

- Minimal Python package, configuration, CLI.
- Provider protocol and deterministic fake provider.
- Gemini Live adapter for text-triggered diagnostic and normalized audio/transcription events.
- Session state machine, error handling, idempotent shutdown.
- Unit tests without external API usage.

## v0.2 Voice Vertical Slice (implemented; Windows headset multi-turn test succeeded)

- sounddevice/PortAudio device abstraction; 16 kHz mono PCM input and 24 kHz PCM output.
- Enter-to-talk/Enter-to-stop toggle, bounded microphone sender and bounded playback buffer.
- Playback flush on Gemini interruption or new turn; clean device shutdown on errors.
- `talk` command streams a microphone-to-Gemini-to-speaker conversation with transcript display.
- Offline tests with fake audio source/sink pass; user confirmed successful Windows headset voice interaction.

## v0.2.3 Read-only local clock (implemented; live voice acceptance pending)

- Offline computer-local clock diagnostic.
- Explicit Gemini Live function declaration and function response.
- Allowlist restricted to the clock; no other computer actions.
- Mock Live round-trip and deterministic timezone tests.
- Real headset test still required for this milestone.

## Phase 1 — Everyday assistance

- **v0.3.0:** SDK-independent strict tool registry; migrate the clock, deny model tool requests by default, block approval-gated actions, bounded in-memory audit metadata, offline `friday tools` diagnostics.
- **Next, one slice at a time:** grounded live web search, weather, and local timers/reminders. Each capability gets validation, permissions and offline tests before live acceptance.
- Google Calendar, GitHub, Spotify, computer controls, MCP expansion, and opt-in personal memory remain future milestones.

## v0.3.1 Long-speech playback (implemented; Windows headset acceptance pending)

- Preserve full PCM speech and stream it at the device rate under bounded backpressure.
- Keep the provider event queue bounded without normal speech eviction.
- Retain the 30-second inactivity watchdog, /quit, and the original terminal prompt.
- Add offline burst and ordering tests before the real headset acceptance check.

## Next Phase 1 slice

- Live web search (proposed v0.3.2), then weather and timers/reminders one at a time.

## Later

- Wake word, local speech pipeline (STT -> LLM -> TTS), persistent SQLite memory.
- Typed tool registry with human approval, per-action policy and audit logs.
- Session resumption and cost/usage telemetry.
- Local desktop interface and hybrid routing only after the core audio path is stable.
