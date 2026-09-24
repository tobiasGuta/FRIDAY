# Changelog

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
