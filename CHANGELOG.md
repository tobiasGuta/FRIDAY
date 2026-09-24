# Changelog

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
