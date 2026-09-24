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

- v0.3.9 implements local, durable one-time schedule storage and an independent terminal-alert worker. Windows acceptance pending.
- v0.4.0 adds explicit desktop OAuth, one-way synchronization to an app-created FRIDAY Google Calendar for iPhone visibility, cancellation propagation, and optional 60-second worker sync. Windows/Google acceptance pending.
- Next: approved voice-created reminders and phone notification reliability, then recurrence and two-way editing.

## v0.3.2–v0.3.7 — Web search (Tavily backend implemented; Windows accepted)

- Search opt-in `talk --web` registers a typed `search_web` function alongside the clock.
- A separate grounded Gemini 3.8 Flash request avoids the native Live Search setup failure (1011); v0.3.5 narrows the delegated Live function declaration after its initial 1007 setup rejection. Search request success and speech remain to be validated on Windows.
- Print supplied grounding source links and show native Google Search Suggestions in a disposable browser preview.
- Test missing or malformed grounding metadata, safe link display, and no-search defaults.
- v0.3.6 detects HTTP 429 and prevents repeat search requests for the current voice session; wait for API quota before retrying.
- v0.3.7 adds Tavily Basic Search as the default optional backend, source snippets, validated HTTPS URLs and a one-shot `web-search` test; the legacy Gemini grounded backend remains opt-in.
- Windows acceptance: Tavily HTTP 200 and spoken answer with sources confirmed.
- Next Phase 1 slice: weather, then timers and reminders.

## v0.3.8 — Weather (Open-Meteo; Windows acceptance pending)

- Two fixed read-only HTTPS API endpoints, geocoding and daily/current forecast.
- Explicit location required; ask for region if ambiguous. Today/tomorrow only.
- No new key for qualifying non-commercial use; attribution required.
- Independent `friday weather` diagnostic; `get_weather` available to
  normal voice sessions without `--web`, no changes to Enter-to-talk.
- No computer-location inference or weather requests without a location query.

## Later

- Wake word, local speech pipeline (STT -> LLM -> TTS), persistent SQLite memory.
- Typed tool registry with human approval, per-action policy and audit logs.
- Session resumption and cost/usage telemetry.
- Local desktop interface and hybrid routing only after the core audio path is stable.
