# Changelog

## 0.5.7 — Hologram Lab, Slice 1 (experimental; Windows acceptance pending)

- Add optional multi-plane amber orbital renderer with an animated energy core,
  existing state-driven animation and restrained outer circuit ticks.
- Keep the accepted v0.5.6 Classic QPainter hologram implementation unchanged
  and selected by default. A checked item in Focus Panels swaps renderers
  locally; on restart the choice safely returns to Classic.
- Keep the animation idle when hidden or disconnected. No microphone amplitude,
  real audio waveform, QML/GL shaders, new API calls, or persistent preference.
- Include Qt rendering tests and fake/no-worker UI regressions. Tiny orbiting
  particles and more extensive state choreography remain later slices.

## 0.5.6 — Voice Focus Mode (merged; Windows user acceptance reported)

- Make the Voice page the opening view while keeping the actual Gemini/microphone
  session disconnected until explicitly connected. Hide dashboard sidebar, top
  bar, conversation list and other tool cards by default.
- Render a larger amber orbital hologram using local Qt painting. The motion
  indicates actual session states only; it is not an acoustic audio meter, a
  movie asset or a transparent desktop overlay.
- Keep the original manual Start talking / Stop recording, explicit
  Connect / Disconnect, bounded full transcript and host-owned reminder draft
  confirmation controls. A small Panels menu and Dashboard button provide
  deterministic escape routes; no second approval implementation.
- Reveal Academic/Reminders only after their allowlisted read-only provider
  tools actually execute or the user gives a narrowly recognized local UI
  display command. Calendar shows only actual scheduler/Google sync status, not
  an invented Google event feed. A later ordinary spoken turn collapses panels.
- Reuse existing in-memory Brightspace records and a bounded read-only reminder
  preview; panel changes do not start a model session, microphone, scheduler or
  network request. Hide unrelated daily cards even while viewing transcript.
- Add offline tests for exact UI-only commands, trusted provider hints, no
  unexpected permissions, focus layout, plaintext subtitles and approval.
- Record exact accepted source SHAs, matching CI and recovery procedure in
  `docs/ACCEPTED_FOCUS_BASELINE.md`. Earlier intermittent Live turn stalls
  remain observable through privacy-safe diagnostics; no permanent cure claimed.

## 0.5.5 — Hybrid desktop shell (Slices 1–3; Windows UI acceptance reported)

- Introduce six navigable PySide6 Widgets pages: Home, Voice, Academic,
  Reminders, Calendar, Settings, with a new dark dashboard style and live
  session/scheduler/Brightspace status bar.
- Reuse one canonical copy of existing voice, reminder approval, Brightspace,
  and scheduler controls; a reminder draft routes to the Voice page instead
  of generating an unapproved parallel workflow.
- Mirror local cached coursework and current-session transcript on Home
  without retrieving Brightspace at startup, storing conversation history,
  or starting a microphone / Gemini session.
- Polish Home with a second decorative state-driven orb, actual upcoming
  academic item cards and original due-label distinctions, a bounded read-only
  local reminder preview, scheduler status, safe quick navigation/manual sync,
  and narrow-window stacked columns. The canonical voice and approval
  controls remain on their own page; the UI adds no fictional actions.
- Redesign Voice as a responsive cinematic stage with larger actual-state
  animated orb, visible connection/recording captions, bounded session-window
  conversation bubbles, a collapsible original raw transcript, and local
  academic/reminder/scheduler context. No audio-level inference or automatic
  microphone connection, no model calls to render UI, no HTML rendering of
  transcript content. Existing approvals and manual audio controls remain.
- Preserve protected feed credential storage, read-only calendar semantics,
  opt-in Google publishing, local scheduler ownership, and quit/tray lifecycle.
- Add headless navigation, approval, offline cache, state-mirroring, and
  plain-text regression tests. Advanced motion, course identification,
  calendar grid, and floating mini-mode remain subsequent UI slices.


## 0.5.4 — Brightspace calendar intelligence (Windows live-feed acceptance pending)

- Add a strictly read-only CUNY Brightspace iCalendar connector. An explicit
  user action saves a private HTTPS subscription in the operating-system
  credential vault; there is no CUNY password or OAuth bypass, no raw feed
  output or logging, and no automatic network access at startup.
- Validate the exact CUNY feed host, disable redirects, enforce decompressed
  response size limits, and parse VEVENT / VTODO with source date semantics.
  Only VTODO DUE is labeled an explicit deadline. VEVENT titles ending
  in ' - Due' carry a separate Brightspace source-label signal, not an
  independently verified submission deadline. Recurring series are flagged
  but not expanded and the calendar does not prove all coursework is covered.
- Store validated snapshots transactionally in separate SQLite storage; failure
  preserves the previously validated snapshot and changing/removing feed
  credentials erases the previous cache. Do not write Google Calendar events.
- Add masked desktop feed field, explicit Sync now and Remove feed, upcoming
  academic agenda, source freshness display, optional 30-minute background
  refresh while the desktop scheduler runs, and opt-in read-only voice lookup.
- Keep Gemini disconnected by default and use cached bounded tool responses
  only when the user enables academic voice lookup before connecting. Convert
  the stored UTC sync instant to the computer's local date/time for spoken
  freshness reports, avoiding midnight UTC date rollover.
- Cover fixtures, credential handling, HTTPS rejection, cache behavior and
  desktop controls offline. Production feed and Windows credential-store
  acceptance remain pending.


## 0.5.3 — Voice reliability and explicit recovery (Windows acceptance pending)

- Detect unexpected Gemini event-stream termination, failed audio sends and
  mid-turn microphone inactivation without waiting for the Stop button.
- Close microphone, speaker and Live session on failure, discard unapproved
  drafts, and retain the independent desktop-managed scheduler.
- Show distinct Connection lost, Audio unavailable and Session expired states.
  Reconnect is a deliberate button action that creates a new Live session; no
  automatic API usage and no false claim of preserved provider context.
- Warn shortly before the configured session duration limit; disallow new
  turns after expiry and allow an in-progress spoken reply up to 30 seconds
  of bounded completion time. Force local teardown at the hard limit.
- Keep manual press-to-talk, existing tool permissions, approval gate, CLI
  commands and no persisted raw audio or conversation memory.
- Add fake-provider, fake-device and headless GUI regressions. Windows
  hardware/long-running headset acceptance remains pending.


## 0.5.2 — Desktop-managed scheduler (Windows acceptance pending)

- Add explicit Start/Stop scheduler controls to the existing desktop window.
  Calendar sync is an opt-in checkbox (unchecked by default); no OAuth browser,
  microphone, Gemini connection, or paid Live session is started by scheduler control.
- Reuse the independent SQLite single-worker lease and APScheduler loop inside a
  separate Qt thread. An existing external CLI worker blocks duplicate desktop
  startup; FRIDAY never stops a worker it did not start.
- Deliver local due timers/reminders using system-tray notifications instead of
  discarding console output in a hidden process. Wait for the UI to attempt a
  notification before acknowledging a reminder; retain bounded retries if
  the tray is unavailable. OS notification delivery is best-effort, not guaranteed.
- Closing the desktop window to the tray leaves the worker running. Explicit
  Quit and no-tray window shutdown cooperatively stop the owned worker and
  release its lease; a full exit stops new local deliveries and phone sync.
- The foreground `schedule worker --calendar-sync` and other CLI commands remain
  unchanged. No Windows startup registration, detached orphan process,
  auto-start, or persistent conversation memory.
- Add offline tests for worker stop, lease safety, notification acknowledgement,
  and desktop ownership controls; Windows hardware acceptance pending.


## 0.5.1 — Windows desktop lifecycle and calendar visibility (Windows acceptance pending)

- Add a Windows desktop shortcut installer using the repository virtual environment's
  `pythonw.exe` to launch the existing GUI without a PowerShell window.
- Add a lightweight programmatic tray icon with Open, Hide, and explicit Quit.
  Closing the window hides it when a system tray is available; Quit requests a
  graceful voice-session shutdown. Without a tray, ordinary close behavior remains.
- Record opt-in calendar worker attempts/outcomes under the active SQLite worker lease
  and display read-only status in the desktop and tray tooltip. Distinguish stopped,
  local-only, legacy/unknown, first sync, syncing, success and failure.
- Read worker status without creating, migrating, or writing the user's database.
  Never launch a second worker, Google OAuth, or calendar sync from the GUI.
- Add offline tests for stale leases, older workers, successful/failed syncs,
  read-only observation and hide/reopen behavior. CI covers Windows and Ubuntu.
- No auto-launch at login, bundled installer, OS toast notifications, wake word,
  or persistent conversation memory; real Windows tray/shortcut acceptance remains pending.


## 0.5.0 — Opt-in Windows desktop voice shell (Windows hardware acceptance pending)

- Add `friday desktop` with optional PySide6 installed via the `desktop` extra.
- Provide a click-to-talk status orb, read-only transcript, upcoming-reminder list,
  and host-owned Confirm/Cancel buttons for pending reminder proposals.
- Run Gemini, microphone, speaker and the existing approval flow on a background
  Qt thread with one asyncio event consumer, buffered playback and graceful close.
- Preserve manual turn boundaries, English input hint, default-deny model tools,
  opt-in web search, and existing `talk` / scheduler commands unchanged.
- Start disconnected; do not open the mic or spend Gemini quota until Connect.
- Add offline session and headless Qt tests on Windows and Ubuntu.
- First slice retains the separate scheduler worker for local alerts and phone sync;
  system tray, wake word and persistent conversation memory are not implemented.


## 0.4.4 — English input transcription hint (Windows acceptance pending)

- Set an explicit `en-US` language hint for `talk` input transcription to
  reduce short English utterances being detected as Spanish.
- Preserve `--input-language auto` for multilingual recognition; text diagnostics
  and other provider consumers keep their previous no-hint default.
- Keep the native AUDIO response modality, microphone framing, and reminder
  approval safeguards unchanged; the hint biases recognition, not guarantees it.
- Add mock Live setup, real SDK schema, and CLI parser regression coverage.


## 0.4.3 — Consistent English voice responses (Windows acceptance pending)

- Prefer American English for spoken responses and output transcripts even when
  automatic input transcription guesses Spanish for short or unclear speech.
- Ask for clarification in English; change response language only when the user
  explicitly requests it. Keep Gemini Live native audio and voice settings intact.
- Add mock Live regression coverage for ordinary and reminder-enabled sessions.


## 0.4.2 — Voice reminder listing, editing, cancellation (Windows acceptance pending)

- Add read-only `get_reminders` and two non-mutating draft functions for edits
  and cancellations; exact IDs must come from the latest list.
- Require human approval in a separate voice turn or `/approve`; a stale draft
  cannot overwrite a reminder changed by another process.
- Add a SQLite revision migration preserving v0.4.1 reminders, exact-ID edit
  CLI, and durable in-place Google Calendar event updates.
- Verify linked Google event ownership before edits, retain the same event ID,
  and retry unacknowledged revisions during worker sync.
- Test old-schema migration, optimistic concurrency, ambiguous reminder names,
  denial paths, and fake-Google updates without accessing real credentials.


## 0.4.1 — Opt-in voice reminder drafts and approval (Windows acceptance pending)

- Add `talk --reminders` with typed, ephemeral, one-at-a-time reminder drafts.
- Validate the future ISO 8601 time with an explicit UTC offset; no schedule is
  written when Gemini merely proposes a draft.
- Require a distinct subsequent user voice turn with exact approval wording, or
  explicit `/approve`; support rejection by voice or `/reject`.
- Only the application writes an approved reminder to the existing SQLite store;
  background Google Calendar sync remains the independent worker's job.
- Discard unapproved drafts on expiry or voice-session shutdown. No raw audio
  or approval transcript is persisted.
- Add offline regression tests. Real Gemini Live and iPhone acceptance pending.

## 0.4.0 — Optional Google Calendar sync (Windows/Google acceptance pending)

- Add explicit Desktop OAuth with the narrow calendar.app.created permission.
- Create a dedicated FRIDAY secondary calendar only when the user requests init.
- One-way publish future pending reminders (not timers) and propagate local
  cancellations, with deterministic event IDs and conflict verification.
- Persist calendar/event links in the existing SQLite database; never replace
  local reminders with remote state.
- Add manual calendar status/connect/init/sync CLI and optional worker
  --calendar-sync (startup and every 60 seconds); no Gemini session required.
- Suppress APScheduler per-second INFO logging without hiding warnings.
- Keep OAuth token outside the repository; no implicit browser or sync.
- Add offline fake-Google regression tests. Real OAuth/iPhone validation pending.


## 0.3.9 — Durable scheduling foundation (Windows acceptance pending)

- Add SQLite-backed one-time timers/reminders with list, cancel and delivery history.
- Add independent APScheduler 3.x worker with a one-worker lease, atomic claims,
  bounded retries and restart recovery; initial notification is console-only.
- Require explicit UTC offset for calendar reminders; never guess ambiguous times.
- Keep the existing voice session, Gemini tools, weather and search unchanged.
- Defer Google Calendar OAuth/sync, phone notifications and recurring schedules.


## 0.3.8 follow-up — Exact city/region weather matching

- Resolve an explicitly named city and state/country against geocoder
  name and administrative fields instead of treating similarly named
  parks and neighborhoods as equally valid locations.
- Keep genuinely duplicate city names ambiguous; never silently select
  a different region when the caller supplied one.
- Add offline regression tests based on the Windows Brooklyn search result.

## 0.3.8 — Read-only weather (Open-Meteo)

- Register `get_weather(location, day)` for normal voice turns, independent
  of the opt-in Tavily `--web` capability.
- Use fixed Open-Meteo geocoding and forecast GET endpoints; no new API key.
  Query today's current conditions and today/tomorrow's daily forecast in
  the location's timezone. Units are °F, mph and percent.
- Require an explicitly named location, return ambiguity options instead
  of guessing, validate input/output, and attribute Open-Meteo.
- Add `friday weather --location ... --day today|tomorrow` standalone diagnostic.
- Keep HTTPS-only endpoints, no redirects, bounded HTTP responses, no raw
  provider errors/locations in HTTP INFO logs and one-session 429 circuit.
- Preserve the voice prompt, local clock, Tavily web search and all existing
  audio behavior. Add mocked HTTP, strict registry and Live round-trip tests.
- Real weather/Windows acceptance is still pending.


## 0.3.7 — Tavily-backed opt-in web search

- Add Tavily Basic Search as the default opt-in `search_web` backend and keep
  Gemini Live for the already-tested voice connection.
- Configure `TAVILY_API_KEY` as a secret; reject a missing key before voice
  hardware opens. The former Gemini grounded-text backend is explicit opt-in.
- Add `friday web-search --query` to test one search without mic or Live credits.
- Make one HTTPS POST to Tavily with up to five results; disable Tavily's extra
  answer synthesis, images and full-page extraction. Bound the response and
  snippets; display only deduplicated HTTPS source links.
- Preserve strict tool argument checks, 429 no-repeat policy, no raw provider
  errors/queries in logs, and the unchanged Enter-to-talk voice flow.
- Add offline HTTP contract tests with a fake transport, including malformed,
  unsafe, rate-limited, oversized and missing-key cases.
- Real Tavily key/credits and Windows headset search acceptance remain pending.


## 0.3.6 — Search quota handling and lint regression

- Fix the Ruff E501 test line-length error from v0.3.5.
- Explicitly disable automatic Python function calling for the separate
  grounded text request, and limit SDK HTTP attempts to one.
- On HTTP 429 return a distinct `search_rate_limited` error and block additional
  search HTTP requests for the current voice session.
- Show a human-readable quota/rate-limit notice rather than a generic rejected
  tool message; instruct Live not to repeat the lookup in the same turn.
- Redact provider exception details and retain the existing clock/voice prompt.
- Add offline test proving repeated calls after 429 do not make more HTTP requests.
- A successful real grounded answer still depends on available Gemini search quota.


## 0.3.5 — Live function declaration schema fix

- Translate typed Pydantic tool argument models to a minimal Gemini Live
  OpenAPI-style function schema rather than forwarding full JSON Schema.
- Omit `additionalProperties`, `minLength`, `maxLength`, and Pydantic titles
  from the Live declaration; retain strict validation in the local tool registry.
- Keep the parameterless clock declaration and default voice mode unchanged.
- Fail closed on unsupported argument shapes; add exact-schema and rejection tests.
- Real `web-check` and grounded search acceptance remain pending on Windows.


## 0.3.4 — Delegated grounded web search

- Replace native Google Search in the Live setup (minimal connection failed with 1011)
  with a read-only, typed `search_web` function.
- Use a separate Gemini 3.8 Flash `generate_content` call with Google's Search tool
  when the user requests current information, without changing normal voice setup.
- Require provider-supplied usable HTTPS sources before claiming a grounded answer.
- Pass bounded answer/source data to Live, and send Search Suggestions only to the UI.
- Enforce query length, blocking-call timeout, redacted errors and opt-in `--web`.
- Extend offline tests for tool configuration, input validation, source handling and
  voice function-call round trips. Real Google/Search/Windows acceptance remains pending.


## 0.3.3 — Web connection diagnostics

- Fix Ruff import ordering in the offline web-search tests.
- Display a safely filtered provider error class and structured status/close code
  on connection failure without logging raw SDK messages, credentials or headers.
- Add `friday web-check` (Search alone) and `friday web-check --with-clock`
  (Search plus clock), connection-only diagnostics requiring no microphone,
  speaker, prompt or search query.
- Leave ordinary `friday talk` and its push-to-talk prompt unchanged. No
  automatic fallback to ungrounded answers or extra paid sessions.


## 0.3.2 — Opt-in Google Search grounding

- Add `talk --web` as explicit opt-in for Google Search in the Gemini Live session.
- Combine native `google_search` with the existing, allowlisted local clock function.
- Display only Google-provided HTTPS source references; never invent a citation.
- Present Google Search Suggestions in a temporary sandboxed browser preview when supplied.
- Remove preview files when the voice session ends; do not build a search-history database.
- Keep the old talk command, clock integration, and push-to-talk prompt unchanged.
- Test opt-in configuration, grounding extraction, rendering, source isolation, and old defaults offline.


## 0.3.1 — Long-speech playback reliability

- Preserve all normal Live PCM speech in arrival order by waiting for speaker capacity instead of dropping oldest audio after four seconds of backlog.
- Apply bounded backpressure to the provider event queue; do not evict speech while the speaker catches up.
- Renew the 30-second no-progress timeout on actual provider/playback progress, while retaining the overall session limit and /quit.
- Report speaker device status events to help diagnose hardware underflows.
- Add offline burst tests for PCM order, queue delivery, and longer responses; preserve manual Enter-to-talk and the clock/tool registry.


## 0.3.0 — Phase 1 tool foundation

- Introduce an SDK-independent, allowlisted tool registry with strict Pydantic argument models.
- Route the existing read-only local clock through the registry without changing its Gemini declaration.
- Hide approval-gated tools from the model and deny execution until a host-side approval flow exists.
- Keep a bounded in-memory audit of tool name/policy/outcome only; no arguments or secrets.
- Add offline `friday tools` introspection and regression tests for typed arguments and Live clock calls.
- Preserve the Enter-to-talk prompt and manual voice-turn completion.


## 0.2.4 — Tool-assisted spoken turn reliability

- Treat a Gemini Live tool-step completion as intermediate until assistant speech
  begins and the final completion arrives; keep push-to-talk closed meanwhile.
- Test both same-message and separate-message intermediate completion sequences.
- Fix Ruff import ordering in the mocked Gemini adapter tests.


## 0.2.3 — Read-only local clock

- Add a parameterless `get_local_time` function that reads the computer's current local time and timezone.
- Register the function in Gemini Live voice and text diagnostic sessions.
- Respond explicitly to each function request; reject unknown names and unexpected arguments.
- Add a no-API `friday clock` diagnostic and deterministic timezone and mock Live API tests.
- Keep Enter-to-talk and microphone/speaker behavior unchanged.


## 0.2.2 — Manual voice-turn boundaries

- Disable automatic VAD only for `talk`, with explicit activity start/end signals.
- Drain queued microphone frames before ending activity; never mix audio stream end into manual mode.
- Preserve turn completion emitted before microphone stop; reset only at the next turn start.
- Add per-turn counts to timeout output without logging keys or raw audio.
- Extend mock SDK and offline voice regression coverage.


## 0.2.1 — Voice-turn reliability (2026-09-23)

- Wait for model turn completion and local speaker-buffer drain before starting another recording.
- Keep /quit responsive, bound terminal-command buffering and discard Enter presses made during playback.
- Add a bounded response timeout and regression tests for overlapping turns, interruption and playback drain.
- Clean up the two import blocks flagged by Ruff in v0.2.

A Windows hardware retest is still required. Acoustic echo cancellation and hands-free barge-in
are not implemented.

## 0.2.0 — Voice Vertical Slice (2026-09-23)

- New opt-in `talk` command with bounded 16 kHz microphone capture and 24 kHz speaker playback.
- Enter-to-talk/stop, multi-turn Gemini Live session and transcript output.
- Interrupt/new-turn playback flush, 40 ms audio frames, device inventory and optional device selection.
- Fake audio hardware and voice-turn regression tests, no API key required.
- Voice integration is awaiting real Windows device / Gemini acceptance testing.

## 0.1.0 — Harness Foundation (2026-09-23)

- New provider-neutral core event and session contracts.
- Offline fake voice provider and CLI demo.
- Optional Gemini Live adapter with text-triggered diagnostic, transcripts, and generated audio events.
- Validated config, safe key handling, bounded event queue, session metrics, and shutdown behavior.
- Automated tests for lifecycle, error handling, SDK event normalization, mock Gemini integration and CLI.

Not included: live microphone or speaker devices, local models, persistent memory, computer control, or a production UI.
