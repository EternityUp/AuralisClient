# Milestone 03: Realtime Barge-In Runtime

Validated on 2026-07-15 with the HP21 Windows WASAPI input/output device pair.

## Scope

This milestone adds a separate validated client/server path without changing the retained Milestone 02 half-duplex loop.

```text
continuous duplex microphone frames
-> server VAD endpoint
-> ASR text and noise filtering
-> short LLM interruption classifier
-> barge_in only for a validated user request
-> client clears persistent duplex playback
-> new user utterance enters the normal LLM/TTS turn path
```

## Why Semantic Confirmation Is Required

Silero VAD is preferred for endpointing but VAD alone cannot determine whether detected speech is an intentional interruption. Residual playback, room noise, coughs, and short acknowledgements would otherwise stop a reply too easily.

The classifier has three decisions:

- `interrupt`: question, request, correction, or explicit stop command.
- `continue`: acknowledgement that should not stop playback.
- `ignore`: noise or invalid/non-directed ASR text.

## Client Behavior

The Windows client requires one WASAPI duplex Stream. It continuously sends 16 kHz mono PCM16 frames while a reply is playing. On a confirmed `barge_in`, it clears queued and active local playback without closing the PortAudio Stream.

The `--seconds` test duration ends capture first, then drains any reply WAV that has already arrived. `Ctrl+C` remains an immediate shutdown that may stop playback mid-reply.

## Validation Result

- One 48 kHz HP21 WASAPI duplex Stream keeps mono microphone capture and stereo playback active together.
- The client continuously sends 16 kHz mono PCM16 frames, including while a reply is playing.
- A short acknowledgement is classified as `continue` and does not clear playback.
- A directed stop command or new question is classified as `interrupt`, clears local playback, and becomes the next conversational turn.
- Normal duration expiry waits for an already received reply WAV to finish before the duplex Stream closes.
- The session ends with `stream_realtime_stopped` rather than a client timeout or PortAudio restart.

## Limitations

- Barge-in remains utterance-level: the user must reach a VAD endpoint before classification.
- The current reply WAV is generated as a complete file before transfer.
- A stale LLM/TTS task is prevented from sending reply audio after interruption, but underlying synchronous model work is not forcibly canceled.
- This protocol requires hardware AEC or headphones for useful speaker-mode evaluation.
