# Changelog

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
