# FRIDAY v0.1 architecture

## Ownership

- **FRIDAY Core** owns state, event delivery, lifecycle and error semantics.
- **Provider adapter** owns the SDK/network connection and normalizes provider output.
- **UI** consumes normalized events; it does not call the Google SDK.
- **Audio device layer** will be introduced later. No device access is implied by the current protocol.

## State machine

`NEW -> CONNECTING -> READY -> CLOSED` or `CONNECTING/READY -> FAILED -> CLOSED`.
Repeated `start()` is invalid; `close()` is idempotent. `metrics()` reports event counts, output audio bytes, elapsed time and dropped events without including user content. Provider exceptions transition to FAILED, emitting an error event. The manager never silently retries a paid API request.

## Event types

`STATE`, `TRANSCRIPT`, `AUDIO`, `TURN_COMPLETE`, `INTERRUPTED`, `NOTICE`, `ERROR`.
`VoiceEvent` contains optional text, speaker, raw 16-bit PCM output bytes, output sample rate and session state. It does not log audio content.

The initial event queue is bounded and single-consumer. When full, incoming audio is dropped; non-audio signals displace the oldest event. This is a foundation-level safety limit, **not** a production-grade audio jitter buffer. The next slice needs dedicated audio queues and explicit overflow telemetry.

## Provider contract

`connect`, `send_text`, `send_audio`, `end_input`, `events`, `close`.
A local provider may implement audio input using offline STT and output using offline TTS. No external provider-specific objects cross this boundary.

## Gemini notes

The adapter uses the supported `client.aio.live.connect()` and `send_realtime_input()` methods, receives transcript and raw audio events, and re-enters `session.receive()` between turns. It does not implement session resumption, VAD configuration, tool calls or speaker playback. Those are separate milestones.

## Security boundaries

The model has no authority over filesystem, shell, browser, email or OS actions in v0.1. Future tools must use a separate typed registry with authorization, logging and explicit result handling. Never trust a tool request just because it originated from the model. No secrets or raw audio should enter logs.
