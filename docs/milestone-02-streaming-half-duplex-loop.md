# Milestone 02: Streaming Half-Duplex Voice Loop

Date: 2026-07-14

## Summary

The first live Auralis voice loop is validated across the Windows client and Linux server:

```text
Windows microphone
-> channel 0 -> continuous 16 kHz mono PCM16 frames
-> WebSocket
-> Silero VAD utterance endpointing
-> sherpa-onnx SenseVoice ASR
-> Ollama qwen3:8b
-> persistent CosyVoice SFT
-> reply WAV over WebSocket
-> Windows speaker playback
```

This is a streaming transport and endpointing milestone. ASR, LLM, and TTS remain utterance-level operations after VAD determines the end of speech.

## Validated Components

- Windows 48 kHz microphone capture with continuous conversion to 16 kHz mono PCM16.
- Channel 0 selection for mono or multichannel client input.
- Binary WebSocket frame transfer over the LAN.
- Silero VAD as the current preferred endpoint detector.
- Server-side utterance WAV persistence and ASR noise filtering.
- Multi-turn LLM context with the latest four user/assistant turns.
- Persistent CosyVoice SFT runtime and reply WAV transfer.
- HP21 WASAPI speaker playback after moving PortAudio stream creation to the main client thread.

## Protocol Events

For a successful user turn the server sends:

```text
turn_started
utterance_saved
asr_result
llm_result
tts_result
reply_audio
<binary WAV bytes>
```

`asr_filtered`, `llm_filtered`, `llm_error`, and `tts_error` end a turn without reply audio; the client resumes capture immediately in those cases.

## Playback Policy

The initial policy is half-duplex:

1. The client streams microphone frames while waiting for speech.
2. After `turn_started`, it closes the input stream and clears queued frames.
3. It receives and plays the completed reply WAV.
4. It reopens the input stream after playback.

This avoids feedback and prevents audio captured during LLM/TTS computation from being interpreted as a new utterance. It is not a claim that the HP21 hardware lacks full-duplex capability; Audacity demonstrates that the device can record and play simultaneously. The current restriction is a deliberate application policy and a PortAudio/WASAPI integration choice.

## Observed Performance

In the validated short-answer turns:

- ASR: about 0.06 to 0.19 seconds.
- LLM: about 0.34 to 1.03 seconds after warmup.
- CosyVoice SFT: about 4.8 to 4.9 seconds for the observed replies.

TTS is therefore the main remaining contribution to perceived response delay.

## Current Limitations

- No partial ASR text, LLM tokens, or progressive TTS audio playback.
- No echo cancellation, barge-in, or speaker interruption.
- Reply WAV is synthesized completely before transfer and playback.
- Device IDs are dynamic and must be rechecked after USB or driver changes.
- The current client is a terminal validation tool, not a packaged desktop application.

## Next Directions

1. Add per-turn latency metrics through playback start.
2. Limit response length and optimize TTS latency.
3. Add explicit playback state and stop control.
4. Evaluate streaming ASR and chunked TTS playback.
5. Add AEC and barge-in only after the half-duplex baseline remains stable.
