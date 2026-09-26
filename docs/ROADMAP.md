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

## v0.5.2 — Desktop-managed scheduler

- Add explicit Start/Stop controls for an owned scheduler in a separate Qt thread,
  independent of the voice session and without a separate PowerShell window.
- Preserve one SQLite worker lease; an existing terminal worker is observed but
  cannot be stopped by the desktop.
- Use system tray alerts for local reminders and timers, with acknowledgement
  only after an attempted tray notification. Windows notification delivery remains
  best-effort and requires acceptance testing.
- Calendar sync is independently opt-in and uses existing Google authorization.
  Hiding the window retains the worker; quitting FRIDAY stops its owned worker.
- No Windows service or auto-start at login; a separate CLI worker remains
  available when FRIDAY is fully exited. Real Windows acceptance pending.

## v0.5.3 — Voice reliability & manual recovery

- Observe failed capture sender and inactive microphone stream while recording;
  stop local devices and close the Live session without waiting for user Stop.
- Treat unexpected provider closure and time-limit expiry as distinct UI states;
  make Reconnect an explicit human action, not an automatic API connection.
- Warn before the configured duration limit, avoid admitting new turns after it,
  and allow only a bounded 30-second tail for a reply already being spoken.
- Retain desktop-managed scheduler and confirmed reminders; discard unapproved
  voice drafts on session shutdown. Do not persist raw audio or conversation history.
- Fake provider/audio/Qt tests; Windows network/headset and longer-session
  acceptance required before merging.

## v0.5.4 — Brightspace calendar intelligence (draft)

- Personal, read-only iCalendar feed with CUNY tenant host allowlist.
- Private subscription saved only in OS credential storage; no raw URL logs,
  plaintext configuration, browser automation, or institutional credentials.
- Bounded validation and transactional local academic snapshot; preserve cached
  data on failures, track last successful sync, and erase cache on disconnect.
- Distinguish VEVENT scheduled times from VTODO explicit DUE, all-day events,
  timezone data, and recurring-series limitations. Never claim the calendar
  is a complete assignment list.
- Masked desktop feed entry, explicit sync, optional refresh only while the
  desktop-managed scheduler runs, and separately opted-in read-only model tool.
- No grade access, submission, Google auto-publishing or persistent voice memory.
- Offline CI and real Windows credential + CUNY feed acceptance required.

## v0.5.5 — Hybrid dashboard and cinematic voice UI

- **Slice 1, implementation branch:** six navigable Qt Widgets pages and a
  dashboard shell, with canonical voice / reminder / Brightspace / scheduler
  controls moved intact. Mirror live state; no new tool permissions, network
  calls on startup, or persistent chat history.
- **Slice 2, implemented on draft branch:** responsive Home voice hero and
  source-backed academic/reminder cards; read-only SQLite preview (no new
  database or startup network), live scheduler summaries and canonical manual
  sync navigation. Windows visual acceptance reported.
- **Slice 3, implemented on draft branch:** larger actual-state cinematic orb,
  responsive voice stage, bounded window-only plain-text conversation bubbles,
  collapsible original transcript and local status context. No live audio
  amplitude claims, auto-listening, auto-reconnect, or new model tools. Windows
  visual/headset acceptance reported.
- **Slice 4:** Academic cards and course filters only when actual feed data
  supports reliable course association; preserve source-labeled versus
  explicit due semantics.
- **Slice 5:** screen-size, keyboard, accessibility, DPI and theme polish.
- Offline CI, then Windows layout/headset/tray/credential acceptance are
  required before merging this UI milestone.

## v0.5.6 — Focus Voice Mode (Windows acceptance reported)

- An orb-first Voice page with normal-window near-black canvas, amber
  state-driven Qt rendering, optional concise subtitles and canonical mic and
  approval controls. Sidebar, full transcript and dashboard cards stay hidden
  until the user requests them.
- Allowlisted successful read-only academic/reminder tool events produce typed
  UI presentation hints; exact local show/hide phrases and visible panel menu
  also work. No transcript keyword scanning for permission changes.
- Academic/reminder drawer uses bounded local data. Calendar drawer shows
  worker and optional Google sync status only, not unrelated event lists.
- Preserve manual Connect/Stop, explicit reconnect, no persistent chat memory,
  no extra Gemini sessions for UI changes, read-only Brightspace and original
  scheduler/tray ownership. No transparent overlay or QML rewrite in scope.
- Offline Windows/Ubuntu CI passed at the accepted v0.5.6 head. User reported
  working Windows focus view, Brightspace drawer and multi-turn conversation.
  Additional long-run stability and individual reminder/scheduler checks remain
  separate regression gates. The exact source and rollback are recorded in
  `docs/ACCEPTED_FOCUS_BASELINE.md`. PR #9 was merged into `main`;
  PR #10 was subsequently merged into `main`, with green offline
  Windows/Ubuntu CI. Future hologram experiments start on separate branches.

## v0.5.7 — Hologram Lab (experimental branch)

- Slice 1: optional Qt painter orbital planes and layered amber energy core;
  Classic v0.5.6 renderer remains default with immediate UI fallback.
- Offline pixel-difference, hidden/idle behavior and no-new-session checks;
  Windows visual/headset acceptance needed before considering release.
- Slice 2A (implemented on separate experimental branch; Windows review pending):
  18 sparse floating golden particles in an independent paint-only toggle;
  Classic remains unchanged and both optional effects default off.
- Slice 2B/2C (not implemented): circuit fragments and subtle light trails,
  only after reviewing Slice 2A on Windows.
- Slice 3 (not implemented): richer motion state transitions and reduced-motion
  preference; evaluate performance rather than assume smoothness.

## v0.5.8 — Project Launcher (experimental branch)

- Dynamic local project registry: manually selected folders and direct children
  of explicitly authorized roots. No hardcoded names, automatic whole-drive
  scan, arbitrary shell commands or auto-coding.
- Desktop Projects page opens VS Code or Windows Terminal after a separate
  confirmation; opt-in Live tools can only list and draft launch requests.
- Offline tests and Windows/Ubuntu CI required; real Windows VS Code/Terminal
  and voice approvals require user acceptance before merge.

## v0.5.9 — Project Status Intelligence (Slice 1, experimental)

- Registered-project-only, bounded local Git summaries on the Projects page,
  independent of Gemini and off the UI thread. Opt-in read-only voice status.
- No filename/path/diff disclosure to the voice model, no Git writes/network,
  and no inferred remote or CI state. Windows device acceptance required.
- Follow-on, independent milestones: combined VS Code + Terminal workspace,
  keyboard summon shortcut, existing-data daily briefing, and opt-in read-only
  GitHub CI/PR status. No favorites or recent-project history.

## Later

- Wake word, local speech pipeline (STT -> LLM -> TTS), persistent SQLite memory.
- Typed tool registry with human approval, per-action policy and audit logs.
- Session resumption and cost/usage telemetry.
- Local desktop interface and hybrid routing only after the core audio path is stable.
