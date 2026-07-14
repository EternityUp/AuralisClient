# Milestone 01: Offline Client/Server Voice Loop

Date: 2026-06-17

This milestone records the first validated end-to-end Auralis client/server voice interaction loop.

> Historical record: this offline baseline remains useful for regression testing. It was superseded as the active interaction path by Milestone 02, documented in `milestone-02-streaming-half-duplex-loop.md`.

## Summary

Auralis has completed the first cross-machine voice interaction milestone:

```text
Win11 Client microphone
-> 16 kHz mono wav
-> WebSocket upload
-> Linux Server ASR
-> Ollama LLM
-> Server TTS
-> WebSocket reply wav
-> Win11 Client speaker playback
```

The validated version is still offline and turn-based:

- the client records a fixed 5-second utterance
- the server processes the complete wav
- the client plays the complete reply wav
- the client then starts the next fixed 5-second recording window

This is not yet a true streaming runtime, but it proves the core client/server architecture is workable.

## Validated Components

### Windows Client

Project path:

```text
E:\AuralisClient
```

Validated capabilities:

- enumerate Windows audio host APIs
- list microphone and speaker devices by host API
- select input and output devices
- record microphone audio
- take channel 0 from mono or multichannel input
- normalize client audio to 16 kHz mono
- save recorded wav files under the project directory
- upload wav files to the Linux server over WebSocket
- receive reply wav audio from the server
- play reply wav audio through the selected speaker
- run repeated offline turns until `Ctrl+C`

Important scripts:

```text
auralis_client/device_test.py
auralis_client/capture_stream_test.py
auralis_client/ws_ping_client.py
auralis_client/upload_wav_client.py
auralis_client/offline_turn_loop_client.py
```

Current multi-turn client command:

```powershell
python -m auralis_client.offline_turn_loop_client --record-seconds 5 --input-device 26 --output-device 23 --server-url ws://192.168.16.206:8765 --timeout 300
```

Playback guard silence is enabled on the client:

```text
head silence: 1000 ms
tail silence: 300 ms
```

This protects against USB/WASAPI playback startup and shutdown truncation. The saved reply wav file is not modified; silence is added only during playback.

### Linux Server

Project path:

```text
/home/xiezc/Auralis
```

Validated capabilities:

- WebSocket connectivity over LAN
- receive uploaded client wav files
- save uploaded wav files
- run ASR
- call Ollama LLM
- synthesize TTS reply audio
- send reply wav bytes back to the Windows client
- keep ASR and TTS loaded persistently in the WebSocket pipeline server

Important scripts:

```text
auralis_lab/ws_ping_server.py
auralis_lab/ws_pipeline_server.py
auralis_lab/runtime.py
```

Current server command:

```bash
cd /home/xiezc/Auralis
conda activate auralis
export PYTHONPATH=/home/xiezc/Auralis/third_party/CosyVoice:$PYTHONPATH
python auralis_lab/ws_pipeline_server.py --host 0.0.0.0 --port 8765
```

Use a specific GPU when needed:

```bash
CUDA_VISIBLE_DEVICES=7 python auralis_lab/ws_pipeline_server.py --host 0.0.0.0 --port 8765
```

## Current Default Stack

```text
ASR: sherpa_onnx + SenseVoice
LLM: Ollama + qwen3:8b
TTS: CosyVoice SFT
```

The WebSocket pipeline server now keeps ASR and TTS as persistent runtime objects. This avoids reloading sherpa-onnx and CosyVoice on every turn.

## Verified Interaction Result

The client/server offline loop was tested for multiple turns. A continuous 11-turn interaction was validated successfully.

Observed behavior:

- client remains active across turns
- after each reply playback, the next 5-second recording window starts
- uploaded client wav files are inspectable
- server-generated reply wav files are inspectable
- actual client speaker playback is complete after adding playback guard silence

## Known Limitations

The current milestone is intentionally not yet a real streaming system.

Current limitations:

- each client turn uses a fixed 5-second recording window
- there is no VAD yet
- audio upload is still wav/message based, not chunked PCM streaming
- server waits for a complete wav before ASR
- LLM response is generated completely before TTS
- TTS reply audio is generated completely before playback
- playback starts only after the full reply wav is received
- barge-in is not supported
- echo cancellation is not implemented

## Important Lessons

### Windows audio devices

The same physical audio device can appear multiple times under different Windows host APIs. The client now groups devices by host API to reduce confusion.

For normal testing, WASAPI or Windows DirectSound are preferred. Bluetooth Hands-Free devices should generally be avoided for quality-sensitive tests.

### ASR input

Client-side audio is normalized to:

```text
16 kHz mono
```

If the input device exposes multiple channels, channel 0 is used.

### Playback

The reply wav files were complete, but real-time playback initially missed the start and end of some replies. This was caused by playback device behavior rather than TTS generation.

The client playback adapter now adds guard silence at playback time:

```text
1000 ms before audio
300 ms after audio
```

### WebSocket protocol

The current prototype supports large wav messages for validation convenience. The production streaming protocol should move to chunked audio frames.

## Next Directions

Recommended next steps:

1. Add server-side VAD so the client no longer needs fixed 5-second recording windows.
2. Replace whole-wav upload with chunked PCM frame upload.
3. Keep the client audio capture stream open and send frames continuously.
4. Have the server segment utterances with VAD.
5. Return reply audio as chunks and play progressively on the client.
6. Add conversation state management and interruption policy.
7. Optimize response length and TTS latency.

This milestone should remain as the stable offline baseline before true streaming work begins.
