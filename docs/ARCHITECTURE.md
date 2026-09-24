# FRIDAY v0.2 architecture

## Ownership

- **FRIDAY Core** owns state, event delivery, lifecycle and error semantics.
- **Provider adapter** owns the SDK/network connection and normalizes provider output.
- **UI** consumes normalized events; it does not call the Google SDK.
- **Audio device layer** opens microphone and speaker only for explicit `talk`; callbacks do not invoke SDK or console.

## State machine

`NEW -> CONNECTING -> READY -> CLOSED` or `CONNECTING/READY -> FAILED -> CLOSED`.
Repeated `start()` is invalid; `close()` is idempotent. `metrics()` reports event counts, output audio bytes, elapsed time and dropped events without including user content. Provider exceptions transition to FAILED, emitting an error event. The manager never silently retries a paid API request.

## Event types

`STATE`, `TRANSCRIPT`, `AUDIO`, `TURN_COMPLETE`, `INTERRUPTED`, `NOTICE`, `ERROR`.
`VoiceEvent` contains optional text, speaker, raw 16-bit PCM output bytes, output sample rate and session state. It does not log audio content.

The initial harness event queue is bounded and single-consumer. Audio input has a separate bounded queue; output has a bounded 24 kHz PCM buffer and callback-driven speaker playback. When full, incoming audio is dropped; non-audio signals displace the oldest event. Overflow counters track dropped microphone chunks and speaker bytes. This is a small prototype, **not** a production-grade jitter buffer or echo cancellation system.

## Provider contract

`connect`, `send_text`, `send_audio`, `start_activity`, `end_activity`, `end_input`, `events`, `close`.
A local provider may implement audio input using offline STT and output using offline TTS. No external provider-specific objects cross this boundary.

## Gemini notes

The adapter uses `client.aio.live.connect()` and `send_realtime_input()`, receives transcript and raw audio events, and re-enters `session.receive()` between turns. `talk` uses explicit activity-start/end and disabled automatic activity detection. Live function calls are answered in the provider adapter through `send_tool_response()`; no model SDK types cross into the core.

## Security boundaries

The model has no authority over filesystem, shell, browser, email or OS actions. The sole model-callable capability is the read-only computer local clock. A strict function-name allowlist and empty argument check reject unexpected requests. No secrets or raw audio should enter logs.

## Voice interaction (v0.2)

`talk` creates a single speaker event consumer, a microphone sender, and a terminal command reader. Each blank Enter toggles between capture and paused state. `Microphone.stop()` appends an EOF marker after queued frames; `VoiceTurns.stop()` waits for the sender to drain before calling provider-neutral `end_activity()`, which Gemini maps to an explicit activity-end signal. Gemini can resume receiving audio for a later turn.

The microphone callback transfers raw bytes to the asyncio loop; the model/network are never called from an audio callback. The speaker's PortAudio callback reads a lock-protected, bounded PCM buffer, pads with silence when empty, and discards pending audio on interruption. The terminal reader is daemonized so a failed network session cannot permanently strand an input thread. Only the read-only clock capability is enabled.


## v0.2.3 local clock function

The only registered Live function is `get_local_time`. The provider receives
Gemini's function call, checks the exact name and empty argument set, reads the
operating system's current date/time with its local UTC offset and timezone name,
and returns the value through `send_tool_response()` with the original function
ID. Unsupported requests receive an explicit error result. The capability has
no geographic-location lookup and no general computer-action interface.

The `clock` CLI command calls the same pure function without opening the
microphone, speakers, Gemini connection, or consuming API quota. The local clock
and timezone are shared with Gemini only when the model invokes the function.
The existing Enter-to-talk state machine and explicit audio activity boundaries
are unchanged.

## v0.3.0 tool registry

`src/friday/tools/registry.py` owns SDK-independent ToolSpec declarations, strict
Pydantic ToolArguments validation, read-only vs approval-required policy, and a
bounded in-memory audit of tool name/policy/outcome. No model-supplied approval
flag can authorize an action. Only read-only tools are advertised in Gemini Live.
The provider dispatches tool responses through the registry; it never dynamically
resolves model-provided names to Python attributes. `get_local_time` remains the
only built-in model-callable tool. `friday tools` provides offline introspection.

## v0.3.1 paced speaker output

The Live event consumer calls `Speaker.enqueue_wait`: it chunks incoming 24 kHz
int16 PCM into the bounded output buffer, awaiting room while the PortAudio
callback drains it in real time. Normal speech is never truncated to the newest
four seconds. The SDK event pump awaits capacity in its bounded event queue,
propagating backpressure rather than silently evicting audio. An intentional
interruption and shutdown may still discard unplayed audio. The existing lossy
`Speaker.enqueue` is retained for explicit latest-audio fallback/tests but is not
used by `friday talk`. The response idle watchdog observes provider progress
and actual played PCM bytes, so a longer answer does not time out simply because
playback is paced; the overall session cap remains in force.

## v0.3.2 Google Search grounding

Native Google Search is separate from the application-owned ToolRegistry, enabled
only by explicit `talk --web`. Live search and `get_local_time` are provided as
separate Gemini tool entries. When the SDK reports grounding metadata, FRIDAY
normalizes HTTPS source URLs and vendor Search Suggestions into a provider-neutral
GROUNDING event. The terminal displays sources with the current answer, and
Google's suggestion markup is shown in a sandboxed browser frame in a temporary
local file removed at session shutdown. No web pages are fetched or indexed by
FRIDAY; grounded content does not become a computer-action instruction. Source
links are not claimed when the provider supplies no usable grounding metadata.
