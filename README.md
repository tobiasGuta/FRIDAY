# FRIDAY

A harness-first personal AI assistant. **v0.3.4 routes opt-in web lookup through a separate grounded text request, preserving the tested Live voice connection.**

No distribution license has been selected yet. Repository visibility is not a grant of reuse rights.

FRIDAY owns the application lifecycle, event types, provider interface and configuration. Gemini Live is an optional provider; a deterministic fake provider enables offline tests. The eventual local voice provider can implement the same contract without leaking SDK-specific types into the core.

## What works today (v0.3.4)

- `friday doctor`: safe configuration diagnostics (never prints your API key).
- `friday demo`: simulated conversation with a fake provider; no network or key needed.
- `friday live`: Gemini Live text-triggered diagnostic with optional WAV output.
- `friday talk`: opt-in live 16 kHz microphone input and 24 kHz speaker output with Enter-to-talk/Enter-to-stop, transcripts, and interruption playback flush.
- `friday devices`: list available PortAudio microphone and speaker device indices.
- `friday clock`: read your computer's local date, time, and configured timezone entirely offline.
- `friday tools`: list enabled application tools and policies without an API key.
- `friday talk --web`: opt in to FRIDAY's `search_web` function. A separate Gemini 3.8 Flash text request performs Google Search grounding; FRIDAY displays actual returned source URLs and a temporary browser preview of Google's Search Suggestions.
- `friday talk` and `friday live`: Gemini may call the narrowly allowlisted `get_local_time` function instead of guessing the current date or time.
- Session state changes, idempotent shutdown, bounded event queue, basic session metrics, and automated tests.

**Not yet implemented:** weather, reminders, wake word, always-on listening, persistent memory, arbitrary computer actions, desktop UI, local inference, session resumption, or hardware-independent echo cancellation. The only model-callable function is the read-only local clock. The `live` diagnostic still writes WAV only; use `talk` to hear FRIDAY automatically.

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
python -m friday clock
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
py -m friday clock
py -m friday tools
py -m friday talk
```

Press **Enter** to activate the microphone, speak, and press **Enter** again to stop recording and prompt Gemini to respond. Repeat for multiple turns. Type `/quit` and Enter to disconnect. The default session limit is 300 seconds, set by `FRIDAY_MAX_SESSION_SECONDS` in `.env` or `--max-seconds` on `talk`. The microphone starts **only** after the user presses Enter; the Gemini Live session connects when `talk` launches. To choose specific devices, use `--input-device INDEX --output-device INDEX` with indices from `devices`.

**Turn sequencing and playback (v0.3.1):** After stopping the microphone, wait for FRIDAY to finish her response. Another ordinary recording cannot begin until Gemini reports completion or interruption and the speaker buffer has drained. Extra Enter presses during playback are ignored; `/quit` remains available. A response stalls after 30 seconds without provider or actual playback progress and displays per-turn audio and event counters without logging raw audio; the configured overall session limit still applies. Long replies use bounded, lossless playback backpressure instead of discarding speech when Gemini sends audio faster than the speakers can play it. Gemini server-side VAD is disabled for `talk`; FRIDAY sends explicit activity start/end around captured PCM. The separate text `live` diagnostic still uses the default Gemini activity mode. This is still toggle-to-talk, not hands-free barge-in. Use headphones to minimize speaker-to-microphone echo; acoustic echo cancellation is not implemented.


### Local clock and tool foundation (v0.3.0)

Ask FRIDAY, “What time is it?” or “What's today's date?” She can call
`get_local_time` and speak a value from your **computer's configured local clock**.
The response includes the operating-system timezone name and UTC offset; it does
not disclose or infer a geographic location, and it is not a general world-time
service. To check the setting without consuming Gemini quota, run:

```powershell
py -m friday clock
```

The tool is read-only and has **no arguments**. In v0.3.0 it runs through a typed, deny-by-default registry; approval-required tools are not advertised or executed until FRIDAY has a real host-side approval flow. A bounded in-memory audit records only tool name, policy, and outcome, never arguments or responses. FRIDAY explicitly rejects unknown
function names or unexpected arguments, and sends the rejection back to Gemini;
she cannot run commands, change system settings, or access your files. The current
clock value and timezone are sent to Gemini only when she calls the tool in a Live
session. The terminal prompt and Enter-to-talk interaction are unchanged.

If the sounddevice/PortAudio backend cannot open a device, FRIDAY reports a local audio error and cleans up rather than silently accessing the wrong device. If Gemini sends an interruption event, pending playback is cleared; pressing Enter for a new turn also clears it. This is a push-to-talk **toggle**, not a hold-to-talk keyboard shortcut.

**Validation boundary:** Offline automated tests cover fake audio device callbacks, PCM framing, buffer limits, two-turn delivery, interruption flush, and cleanup. They do not prove that an individual Windows microphone, sound driver, or Gemini account works; test with real hardware and your own key. The `talk` command uses API quota. No local raw recordings are persisted by default.

### Opt-in grounded web search (v0.3.4)

Search is **off by default**. To enable it for one voice session:

```powershell
py -m friday talk --input-device 1 --web
```

Ask, “Friday, search the web for the latest Python release and give me a source.”
FRIDAY's already-working Live connection exposes a strictly validated, read-only
`search_web` function alongside `get_local_time`. When requested, that
function makes a **separate** Gemini `generate_content` text request with Google's
Search grounding tool, using `FRIDAY_SEARCH_MODEL` (default `gemini-3.8-flash`).
Native Google Search is **not** declared on the Live connection: on this
project/model, even a minimal native-Search connection returned APIError 1011.
This workaround does not claim to fix that upstream failure.

The application passes an answer to the voice model only if Google returns
usable HTTPS source URLs. If Search fails or grounding contains no usable URLs,
FRIDAY reports an error rather than inventing citations. Actual source links
appear in the terminal; Google Search Suggestions, if returned, appear in a
disposable sandboxed local browser preview. Preview files are removed after
the voice session. No search history is stored. The tool request sends the
model-selected question to a second text API request, which may incur separate
model/Search charges. It does not fetch arbitrary links, follow instructions
embedded in web content, or grant computer control.

```powershell
# Without a microphone or search query; checks the delegated Live tool declaration:
py -m friday web-check
py -m friday web-check --with-clock
```

A passing `web-check` verifies the Live function setup only, **not** that
the separate grounded text request or resulting speech works. The full acceptance
test is `talk --web` with a real question. The usual `talk` command
and the exact `FRIDAY [Enter: talk/stop, /quit]:` prompt are unchanged.

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

- The model sees only explicitly registered read-only tools; currently the local clock. Unknown and invalid calls are rejected. Approval-required tools are not executable.
- No arbitrary shell, file, email, or network actions are exposed to the LLM.
- The single allowlisted model tool reads only OS local date/time and timezone.
- No raw audio is logged; audio event `repr` reports only byte length.
- SDK imports are confined to `src/friday/providers/gemini_live.py`.
- `.env`, `.wav`, `.pcm`, and virtual environments are ignored by Git.
- Event queue is bounded. Live provider audio waits for queue capacity rather than evicting words; urgent lifecycle/error signals can still displace queued events. Microphone input and speaker playback each have bounded buffering. Speaker output uses paced, non-dropping enqueue during normal speech, and immediate flush for an explicit interruption or shutdown.

## Running checks

```bash
python -m pytest -q
python -m compileall -q src tests
ruff check .
```

For a fake-provider smoke test: `python -m friday demo --once "Hello"`.

## Scope

v0.2 is an opt-in voice vertical slice. Real Windows microphone/speaker behavior, repeated Gemini turns, and resource cleanup are the current acceptance target; wake word, memory, and computer actions follow after stabilization.
