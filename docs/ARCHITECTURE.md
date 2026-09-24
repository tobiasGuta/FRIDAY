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

`connect`, `send_text`, `send_audio`, `end_input`, `events`, `close`.
A local provider may implement audio input using offline STT and output using offline TTS. No external provider-specific objects cross this boundary.

## Gemini notes

The adapter uses the supported `client.aio.live.connect()` and `send_realtime_input()` methods, receives transcript and raw audio events, and re-enters `session.receive()` between turns. It does not implement session resumption, VAD configuration, tool calls or speaker playback. Those are separate milestones.

## Security boundaries

The model has no authority over filesystem, shell, browser, email or OS actions in v0.1. Future tools must use a separate typed registry with authorization, logging and explicit result handling. Never trust a tool request just because it originated from the model. No secrets or raw audio should enter logs.

## Voice interaction (v0.2)

`talk` creates a single speaker event consumer, a microphone sender, and a terminal command reader. Each blank Enter toggles between capture and paused state. `Microphone.stop()` appends an EOF marker after queued frames; `VoiceTurns.stop()` waits for the sender to drain before calling provider-neutral `end_input()`, which Gemini maps to `audio_stream_end=True` with server VAD enabled. Gemini can resume receiving audio for a later turn.

The microphone callback transfers raw bytes to the asyncio loop; the model/network are never called from an audio callback. The speaker's PortAudio callback reads a lock-protected, bounded PCM buffer, pads with silence when empty, and discards pending audio on interruption. The terminal reader is daemonized so a failed network session cannot permanently strand an input thread. No tool execution is enabled.
