# FRIDAY

A harness-first personal AI assistant. **v0.1 is the foundation, not yet a microphone-driven Jarvis clone.**

No distribution license has been selected yet; decide that before publishing this repository.

FRIDAY owns the application lifecycle, event types, provider interface and configuration. Gemini Live is an optional provider; a deterministic fake provider enables offline tests. The eventual local voice provider can implement the same contract without leaking SDK-specific types into the core.

## What works today

- `friday doctor`: safe configuration diagnostics (never prints your API key).
- `friday demo`: simulated conversation with a fake provider; no network or key needed.
- `friday live`: optional Gemini Live text-triggered session, receives transcript/audio events; can explicitly save the 24 kHz PCM output in a WAV file.
- Session state changes, idempotent shutdown, bounded event queue, basic session metrics, and automated tests.

**Not yet implemented:** microphone capture, speaker playback, wake word, persistent memory, tool execution, desktop UI, local inference, session resumption, or production-grade buffering. The live diagnostic sends text and can save generated speech; it does not play it automatically.

## Requirements

Python 3.11+; the first tested build targets Python 3.12/3.13. `uv` is convenient but ordinary `pip` also works.

```bash
# from the project directory
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m friday doctor
python -m friday demo --once "Hello FRIDAY"
python -m pytest
```

On PowerShell, `'.[dev]'` works as written. For `uv`, run `uv sync --extra dev` followed by `uv run friday demo`.

## Optional Gemini Live diagnostic

Install `python -m pip install -e '.[gemini,dev]'`. Copy `.env.example` to `.env`, enter a **new or existing** Google AI Studio key as `GEMINI_API_KEY`, and keep `.env` untracked. Verify the model ID and quota in your AI Studio project; the default is `gemini-3.8-live` as documented in Google's September 2026 Live API guide.

```bash
python -m friday live --text "Friday, introduce yourself briefly"
python -m friday live --text "Say hello" --output friday-hello.wav
```

A Gemini Live session uses network/API quota. Do not use private recordings or secrets as prompts without considering the provider's current data handling terms. Only `--output` saves generated audio; raw input audio is not implemented or stored.

## Architecture

```text
CLI / future desktop UI
       |
SessionManager (state, errors, bounded event queue)
       |
VoiceProvider protocol (send_text, send_audio, end_input, events, close)
       +-- FakeVoiceProvider (offline tests)
       +-- GeminiLiveProvider (SDK isolated here)
       +-- Future local pipeline (STT -> LLM -> TTS)
```

For the event contract and security boundaries, see `docs/ARCHITECTURE.md`; for the next development slice, see `docs/ROADMAP.md`.

## Safety defaults

- No tools are registered; unexpected model tool-call messages are ignored.
- No arbitrary shell, file, email, or network actions are exposed to the LLM.
- No raw audio is logged; audio event `repr` reports only byte length.
- SDK imports are confined to `src/friday/providers/gemini_live.py`.
- `.env`, `.wav`, `.pcm`, and virtual environments are ignored by Git.
- Event queue is bounded. Overloaded consumers may drop events; audio will get its own streaming queue in the next slice.

## Running checks

```bash
python -m pytest -q
python -m compileall -q src tests
ruff check .
```

For a fake-provider smoke test: `python -m friday demo --once "Hello"`.

## Scope

v0.1 is deliberately a minimal harness foundation. See the roadmap before adding features; the next vertical slice is microphone -> Gemini Live -> speakers with cancellation and bounded audio queues.
