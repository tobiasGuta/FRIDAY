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
- v0.4.1: opt-in voice reminder drafts and host-validated approval; Windows/iPhone creation accepted.
- v0.4.2: pending reminder listing, editing and cancellation by voice with exact-ID proposals, one-at-a-time approval and revision-aware Google updates; Windows acceptance pending.
- Next: phone notification reliability, then recurrence and two-way editing.

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

## v0.5.0 — Desktop voice vertical slice

- Opt-in PySide6 desktop UI with click-to-talk, status orb, transcript, pending
  reminder cards and host-owned approval buttons. Qt is an optional extra.
- Run the existing Gemini/PortAudio voice session off the UI thread and preserve
  manual turn sequencing and safe shutdown; CLI voice remains available.
- The existing separate scheduler continues local notifications and optional
  Google Calendar sync. Real Windows window/mic acceptance is pending.
- Later UI slices: embedded worker controls, tray/background lifecycle, OS
  notifications, device selection and optional wake word.

## v0.5.1 — Desktop lifecycle and calendar worker visibility

- Windows shortcut explicitly installed using the project virtual environment
  and console-free `pythonw.exe`; no system startup registration.
- Tray Open/Hide/Quit handles window visibility separately from Live connection
  lifecycle. Quit stops the active audio/voice session; no tray retains normal close.
- Scheduler records lease-bound, non-sensitive calendar attempt/outcome metadata.
  Desktop reads it without creating or changing the SQLite database, and does not
  launch another scheduler process. Phone sync remains a separately opted-in worker.
- Offline coverage added; Windows desktop shortcut, tray, and real iPhone
  synchronization acceptance remain to be confirmed.

## Later

- Wake word, local speech pipeline (STT -> LLM -> TTS), persistent SQLite memory.
- Typed tool registry with human approval, per-action policy and audit logs.
- Session resumption and cost/usage telemetry.
- Local desktop interface and hybrid routing only after the core audio path is stable.
