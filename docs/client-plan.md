# Auralis Client Plan

This document tracks the Windows client implementation plan.

## Current Status

Milestone 02 is validated as of 2026-07-14. The Windows client can continuously capture channel 0, resample it to 16 kHz mono PCM16, send frames to the Linux server, receive utterance-level reply WAV files, and play them through the selected speaker.

The current live loop is deliberately half-duplex:

```text
capture -> server accepts utterance -> close capture stream
-> receive and play reply WAV -> reopen capture stream
```

This avoids feedback and keeps WASAPI playback on the main Windows thread. It is the stable baseline before full-duplex echo cancellation or barge-in.

## Scope

The client handles Windows-side audio interaction only.

Responsibilities:

- enumerate available microphone devices
- enumerate available speaker devices
- let the user select one microphone and one speaker
- capture audio from the selected microphone
- take channel 0 when the input is multichannel
- resample captured audio to 16 kHz mono
- send processed audio to the server
- receive synthesized audio from the server
- play audio through the selected speaker

Out of scope for the first client milestone:

- VAD
- ASR
- LLM
- TTS
- local model inference

These remain on the Linux Auralis server.

## Milestones

### Step 1: Local Device Validation

Goal:

```text
list mic/spk devices
select mic/spk
record 3 seconds from mic
save 16 kHz mono wav
play the saved wav through speaker
```

### Step 2: Client Audio Capture Loop

Goal:

```text
selected mic -> continuous frames -> channel 0 -> 16 kHz mono frames
```

Validation command:

```powershell
python -m auralis_client.capture_stream_test
```

Expected result:

- capture runs until `Ctrl+C`
- captured audio is saved as `outputs/capture-stream-test.wav`
- saved audio is 16 kHz mono
- playback through the selected speaker sounds continuous

### Step 3: Server Connectivity Test

Goal:

```text
connect to ws://192.168.16.206:8765
send a small test message
receive a server response
```

Server command:

```bash
cd /home/xiezc/Auralis
python -m pip install -r requirements/realtime.txt
python auralis_lab/ws_ping_server.py --host 0.0.0.0 --port 8765
```

Client command:

```powershell
python -m auralis_client.ws_ping_client --server-url ws://192.168.16.206:8765
```

Expected response type:

```text
pong
```

### Step 4: Utterance Upload Prototype

Goal:

```text
record one short utterance
send wav/pcm bytes to server
receive reply audio
play reply audio
```

Current validation command:

```powershell
python -m auralis_client.upload_wav_client --wav outputs/capture-stream-test.wav --server-url ws://192.168.16.206:8765
```

Or:

```powershell
python -m auralis_client.upload_wav_client --record-seconds 3 --input-device 30 --server-url ws://192.168.16.206:8765
```

Expected server response:

```text
audio_upload_ack
```

Note: this prototype may allow large WebSocket messages for convenience. The next protocol should use chunked audio upload or streaming PCM frames.

To validate server-to-client reply audio, start the temporary server with `--reply-wav`, then run:

```powershell
python -m auralis_client.upload_wav_client --wav outputs/capture-stream-test.wav --server-url ws://192.168.16.206:8765 --expect-reply-audio --reply-output outputs/server-reply.wav --output-device 22
```

### Step 5: Full Client Runtime

Goal:

```text
mic capture -> server websocket -> reply audio -> speaker playback
```

VAD is expected to run on the server in this stage.

Before the full streaming runtime, validate one offline turn:

```powershell
python -m auralis_client.upload_wav_client --record-seconds 5 --input-device 30 --server-url ws://192.168.16.206:8765 --expect-reply-audio --reply-output outputs/offline-turn-reply.wav --output-device 22 --timeout 300
```

Then validate repeated offline turns:

```powershell
python -m auralis_client.offline_turn_loop_client --record-seconds 5 --input-device 26 --output-device 23 --server-url ws://192.168.16.206:8765 --timeout 300
```

This keeps the client alive until `Ctrl+C`. Each turn records a fixed-length utterance, waits for the server reply, plays the reply, and then starts the next recording window.

### Step 6: Streaming Frame Upload Prototype

Goal:

```text
client continuous mic capture
-> supported source sample rate
-> stateful resampling when needed
-> 16 kHz mono PCM16 frames
-> WebSocket binary messages
-> server reconstructs and saves wav
```

Server command:

```bash
python auralis_lab/ws_stream_record_server.py --host 0.0.0.0 --port 8766
```

Client command:

```powershell
python -m auralis_client.stream_upload_client --input-device 26 --server-url ws://192.168.16.206:8766 --seconds 10 --frame-ms 100
```

The client first tries to capture at 16 kHz, then falls back to the device default or common desktop rates such as 48 kHz. When the source rate differs from 16 kHz, it uses a continuous streaming resampler before emitting fixed-size PCM16 frames.

### Step 7: Server VAD and Streaming ASR

Completed and validated with Silero VAD and `sherpa_onnx + SenseVoice`. The client remains responsible only for continuous 16 kHz PCM16 frame delivery; utterance segmentation and ASR stay on the server.

### Step 8: Streaming LLM and TTS Reply Playback

Completed and validated with Ollama `qwen3:8b` and persistent CosyVoice SFT. The client receives `reply_audio` metadata followed by WAV bytes, saves the WAV under `outputs/stream_replies/`, plays it, then resumes capture.

See `milestone-02-streaming-half-duplex-loop.md` for the validated cross-machine protocol and current limitations.

### Step 9: Single WASAPI Duplex Stream

The local HP21 hardware test passed: one `sounddevice.Stream` kept 48 kHz WASAPI capture and stereo playback active simultaneously while input was continuously converted to 16 kHz mono PCM16.

`stream_upload_client.py` now adopts this engine automatically when both selected devices are WASAPI endpoints. It retains the existing interaction policy by pausing only upstream frame delivery during server processing and reply playback; the hardware Stream remains open. Use `--audio-mode separate_streams` only as a compatibility fallback.

The next validation is the full server-backed loop using `--audio-mode duplex_wasapi`.
