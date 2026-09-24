# FRIDAY

A harness-first personal AI assistant. **v0.2.2 stabilizes manual push-to-talk; v0.2 adds an opt-in live microphone-to-Gemini-to-speaker voice path.**

No distribution license has been selected yet. Repository visibility is not a grant of reuse rights.

FRIDAY owns the application lifecycle, event types, provider interface and configuration. Gemini Live is an optional provider; a deterministic fake provider enables offline tests. The eventual local voice provider can implement the same contract without leaking SDK-specific types into the core.

## What works today (v0.2)

- `friday doctor`: safe configuration diagnostics (never prints your API key).
- `friday demo`: simulated conversation with a fake provider; no network or key needed.
- `friday live`: Gemini Live text-triggered diagnostic with optional WAV output.
- `friday talk`: opt-in live 16 kHz microphone input and 24 kHz speaker output with Enter-to-talk/Enter-to-stop, transcripts, and interruption playback flush.
- `friday devices`: list available PortAudio microphone and speaker device indices.
- Session state changes, idempotent shutdown, bounded event queue, basic session metrics, and automated tests.

**Not yet implemented:** wake word, always-on listening, persistent memory, tool execution, desktop UI, local inference, session resumption, or hardware-independent echo cancellation. The `live` diagnostic still writes WAV only; use `talk` to hear FRIDAY automatically.

## Requirements

Python 3.11+; the offline suite is verified here on Python 3.13. Windows audio hardware is validated separately on your machine. `uv` is convenient but ordinary `pip` also works.

```bash
# from the project directory
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate
python -m pip install -e '.[gemini,voice,dev]'
python -m friday doctor
python -m friday demo --once "Hello FRIDAY"
python -m pytest
```

On PowerShell, `'.[gemini,voice,dev]'` works as written. With `uv`, install the gemini, voice, and dev extras.

## Optional Gemini Live diagnostic

Install `python -m pip install -e '.[gemini,voice,dev]'`. Copy `.env.example` to `.env`, enter a **new or existing** Google AI Studio key as `GEMINI_API_KEY`, and keep `.env` untracked. Verify the model ID and quota in your AI Studio project; the default is `gemini-3.8-live` as documented in Google's September 2026 Live API guide.

```bash
python -m friday live --text "Friday, introduce yourself briefly"
python -m friday live --text "Say hello" --output friday-hello.wav
```

A Gemini Live session uses network/API quota. Do not use private recordings or secrets as prompts without considering the provider's current data handling terms. Only `--output` saves generated audio; raw input audio is not implemented or stored.

## First live voice conversation (Windows PowerShell)

Use **headphones** because this version does not implement echo cancellation.

```powershell
git pull --ff-only
py -m pip install -e '.[gemini,voice,dev]'
py -m pytest -q
py -m friday devices
py -m friday talk
```

Press **Enter** to activate the microphone, speak, and press **Enter** again to stop recording and prompt Gemini to respond. Repeat for multiple turns. Type `/quit` and Enter to disconnect. The default session limit is 300 seconds, set by `FRIDAY_MAX_SESSION_SECONDS` in `.env` or `--max-seconds` on `talk`. The microphone starts **only** after the user presses Enter; the Gemini Live session connects when `talk` launches. To choose specific devices, use `--input-device INDEX --output-device INDEX` with indices from `devices`.

**Turn sequencing (v0.2.2):** After stopping the microphone, wait for FRIDAY to finish her response. Another ordinary recording cannot begin until Gemini reports completion or interruption and the speaker buffer has drained. Extra Enter presses during playback are ignored; `/quit` remains available. A response that never completes times out after 30 seconds and displays per-turn audio and event counters without logging raw audio. Gemini server-side VAD is disabled for `talk`; FRIDAY sends explicit activity start/end around captured PCM. The separate text `live` diagnostic still uses the default Gemini activity mode. This is still toggle-to-talk, not hands-free barge-in. Use headphones to minimize speaker-to-microphone echo; acoustic echo cancellation is not implemented.

If the sounddevice/PortAudio backend cannot open a device, FRIDAY reports a local audio error and cleans up rather than silently accessing the wrong device. If Gemini sends an interruption event, pending playback is cleared; pressing Enter for a new turn also clears it. This is a push-to-talk **toggle**, not a hold-to-talk keyboard shortcut.

**Validation boundary:** Offline automated tests cover fake audio device callbacks, PCM framing, buffer limits, two-turn delivery, interruption flush, and cleanup. They do not prove that an individual Windows microphone, sound driver, or Gemini account works; test with real hardware and your own key. The `talk` command uses API quota. No local raw recordings are persisted by default.

## Architecture

```text
CLI / future desktop UI + opt-in PortAudio mic/speaker
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
- Event queue is bounded. Overloaded consumers may drop events; microphone input and speaker playback each have bounded buffering.

## Running checks

```bash
python -m pytest -q
python -m compileall -q src tests
ruff check .
```

For a fake-provider smoke test: `python -m friday demo --once "Hello"`.

## Scope

v0.2 is an opt-in voice vertical slice. Real Windows microphone/speaker behavior, repeated Gemini turns, and resource cleanup are the current acceptance target; wake word, memory, and computer actions follow after stabilization.
