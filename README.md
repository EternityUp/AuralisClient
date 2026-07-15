# Auralis Client

Auralis Client is the Windows-side audio frontend for Auralis.

It is responsible for:

- listing microphone and speaker devices
- capturing microphone audio
- selecting channel 0 from mono or multichannel input
- converting captured audio to 16 kHz mono
- sending processed audio to the Auralis server
- receiving synthesized reply audio from the server
- playing reply audio through the selected speaker

The server-side Auralis project remains responsible for:

- VAD
- ASR
- LLM
- TTS
- model runtime

## Planned Runtime Shape

```text
Win11 AuralisClient
  mic -> channel 0 -> 16 kHz mono -> WebSocket

Linux Auralis Server
  VAD -> ASR -> LLM -> TTS

Win11 AuralisClient
  WebSocket reply audio -> speaker
```

## Environment

Recommended Python version:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel
python -m pip install -r requirements.txt
```

## First Milestone

The first milestone is local audio I/O validation:

```text
list devices -> select mic/spk -> record 3 seconds -> save 16 kHz mono wav -> play wav
```

Run:

```powershell
python -m auralis_client.device_test
```

List devices without recording:

```powershell
python -m auralis_client.device_test --list-devices
```

On Windows, the same physical device may appear multiple times because it is exposed through different audio host APIs such as MME, Windows DirectSound, WASAPI, and WDM-KS. The interactive device test first asks for the host API, then lists microphone and speaker devices under that API.

For normal Windows desktop use, try WASAPI or Windows DirectSound first. Avoid Bluetooth Hands-Free devices when possible because they often expose 8 kHz or 16 kHz telephony-quality audio.

## Continuous Capture Test

After device validation works, test continuous capture:

```powershell
python -m auralis_client.capture_stream_test
```

It captures microphone audio until `Ctrl+C`, converts it to 16 kHz mono, saves it to:

```text
outputs/capture-stream-test.wav
```

Then it plays the captured wav through the selected speaker unless `--no-playback` is set.

## WebSocket Connectivity Test

Start the server on the Linux Auralis machine:

```bash
cd /home/xiezc/Auralis
python -m pip install -r requirements/realtime.txt
python auralis_lab/ws_ping_server.py --host 0.0.0.0 --port 8765
```

Then test from Windows:

```powershell
python -m auralis_client.ws_ping_client --server-url ws://192.168.16.206:8765
```

Expected response:

```text
SERVER_URL: ws://192.168.16.206:8765
ROUND_TRIP_MS: ...
RESPONSE:
{"type": "pong", ...}
```

## WAV Upload Test

After WebSocket ping/pong works, upload a 16 kHz mono wav to the server:

```powershell
python -m auralis_client.upload_wav_client --wav outputs/capture-stream-test.wav --server-url ws://192.168.16.206:8765
```

Or record a short temporary wav and upload it:

```powershell
python -m auralis_client.upload_wav_client --record-seconds 3 --input-device 30 --server-url ws://192.168.16.206:8765
```

The server saves uploaded audio under:

```text
outputs/ws_uploads/
```

The current upload prototype allows large WebSocket messages for validation convenience. The production streaming protocol should use chunked audio frames instead of sending long wav files as one message.

To simulate server-side TTS audio return, start the server with a reply wav:

```bash
python auralis_lab/ws_ping_server.py --host 0.0.0.0 --port 8765 --reply-wav outputs/cosyvoice-sft.wav
```

Then upload audio and expect a reply wav:

```powershell
python -m auralis_client.upload_wav_client --wav outputs/capture-stream-test.wav --server-url ws://192.168.16.206:8765 --expect-reply-audio --reply-output outputs/server-reply.wav --output-device 22
```

## Offline Full-Turn Test

After upload and simulated reply audio are validated, start the server-side offline pipeline:

```bash
cd /home/xiezc/Auralis
export PYTHONPATH=/home/xiezc/Auralis/third_party/CosyVoice:$PYTHONPATH
python auralis_lab/ws_pipeline_server.py --host 0.0.0.0 --port 8765
```

This server keeps ASR and TTS loaded persistently after startup. Restart it after code changes, but do not restart it between turns.

Then record a short utterance on Windows, upload it, receive the real ASR/LLM/TTS reply, and play it:

```powershell
python -m auralis_client.upload_wav_client --record-seconds 5 --input-device 26 --server-url ws://192.168.16.206:8765 --expect-reply-audio --reply-output outputs/offline-turn-reply.wav --output-device 23 --timeout 300
```

This is still an offline single-turn test: the client records a fixed-length wav first, then the server runs ASR, LLM, and TTS.

## Offline Multi-Turn Loop Test

Use this after the single-turn test works. The client keeps running until `Ctrl+C`; each turn records a fixed 5-second utterance, sends it to the server, receives the ASR/LLM/TTS reply audio, plays it, then starts the next 5-second recording window.

Start the server as in the offline full-turn test, then run:

```powershell
python -m auralis_client.offline_turn_loop_client --record-seconds 5 --input-device 26 --output-device 23 --server-url ws://192.168.16.206:8765 --timeout 300
```

Recorded client inputs are saved under `outputs/turn_inputs/`, and server reply audio files are saved under `outputs/turn_replies/`.

## Project Notes

- See `docs/client-plan.md` for the Windows client implementation plan.
- See `docs/milestone-01-offline-client-server-loop.md` for the first validated client/server voice loop milestone.
- See `docs/milestone-02-streaming-half-duplex-loop.md` for the validated live streaming loop milestone.

## Streaming Frame Upload Test

This is the first Milestone 02 test. It only validates continuous PCM frame upload and server-side wav reconstruction. It does not run VAD, ASR, LLM, or TTS yet.

Start the stream record server on Linux:

```bash
cd /home/xiezc/Auralis
python auralis_lab/ws_stream_record_server.py --host 0.0.0.0 --port 8766
```

Run the Windows client:

```powershell
python -m auralis_client.stream_upload_client --input-device 26 --server-url ws://192.168.16.206:8766 --seconds 10 --frame-ms 100 --blocksize-frames 0
```

The client opens the selected microphone at a supported source sample rate, converts channel 0 to continuous 16 kHz mono PCM16 frames, and sends those frames to the server. The server saves reconstructed wav files under `outputs/ws_stream_records/`.

## Streaming VAD Endpoint Test

This validates continuous stream segmentation before ASR/LLM/TTS are added.

Start one VAD server on Linux:

```bash
cd /home/xiezc/Auralis
python auralis_lab/ws_stream_vad_server.py --host 0.0.0.0 --port 8767 --vad-engine energy
```

FunASR FSMN-VAD and Silero VAD can also be selected:

```bash
python auralis_lab/ws_stream_vad_server.py --host 0.0.0.0 --port 8767 --vad-engine funasr_fsmn
python auralis_lab/ws_stream_vad_server.py --host 0.0.0.0 --port 8767 --vad-engine silero
python auralis_lab/ws_stream_vad_server.py --host 0.0.0.0 --port 8767 --vad-engine webrtc --webrtc-aggressiveness 2
```

Server-side VAD model files are expected under `models/vad/`:

```text
models/vad/funasr-fsmn-vad
models/vad/silero-vad
```

WebRTC VAD has no model file and only needs the `webrtcvad` Python package.

Run the Windows client:

```powershell
python -m auralis_client.stream_upload_client --input-device 26 --server-url ws://192.168.16.206:8767 --seconds 30 --frame-ms 100 --blocksize-frames 0
```

Detected utterances are saved on the server under `outputs/ws_stream_utterances/`.

## Streaming VAD + ASR Test

This validates continuous stream segmentation plus ASR transcription. The client still only streams microphone frames and prints server events.

Start the server on Linux:

```bash
cd /home/xiezc/Auralis
python auralis_lab/ws_stream_asr_server.py --host 0.0.0.0 --port 8768 --vad-engine silero --asr-engine sherpa_onnx
```

Run the Windows client:

```powershell
python -m auralis_client.stream_upload_client --input-device 26 --server-url ws://192.168.16.206:8768 --seconds 60 --frame-ms 100 --blocksize-frames 0
```

The client prints `utterance_saved`, `asr_result`, and `asr_filtered` events. Utterance wav files are saved on the server under `outputs/ws_stream_asr_utterances/`.

## Streaming VAD + ASR + LLM Test

This adds Qwen3 replies after valid ASR results. `asr_filtered` events do not enter the LLM stage.

Start the server on Linux:

```bash
cd /home/xiezc/Auralis
python auralis_lab/ws_stream_llm_server.py --host 0.0.0.0 --port 8769 --vad-engine silero --asr-engine sherpa_onnx --llm-model qwen3:8b
```

Run the Windows client:

```powershell
python -m auralis_client.stream_upload_client --input-device 26 --server-url ws://192.168.16.206:8769 --seconds 90 --frame-ms 100 --blocksize-frames 0 --timeout 180
```

The client prints `asr_result`, `asr_filtered`, and `llm_result` events. A connection keeps the latest four user/assistant turns by default; add `--max-history-turns 0` to the server command for independent single-turn replies.

## Streaming VAD + ASR + LLM + TTS Test

This is the first full streaming voice loop. The server keeps Sherpa-ONNX ASR and CosyVoice loaded, then returns each synthesized WAV reply to the Windows client for playback.

Start the server on Linux. Set `PYTHONPATH` so the local CosyVoice checkout can be imported. If GPU 7 is the intended TTS GPU, retain `CUDA_VISIBLE_DEVICES=7`; otherwise omit that prefix.

```bash
cd /home/xiezc/Auralis
export PYTHONPATH=/home/xiezc/Auralis/third_party/CosyVoice:$PYTHONPATH
CUDA_VISIBLE_DEVICES=7 python auralis_lab/ws_stream_tts_server.py --host 0.0.0.0 --port 8770 --vad-engine silero --asr-engine sherpa_onnx --llm-model qwen3:8b --tts-engine cosyvoice --cosy-mode sft
```

Run the Windows client with the selected microphone and speaker IDs:

```powershell
python -m auralis_client.stream_upload_client --input-device 26 --output-device 23 --server-url ws://192.168.16.206:8770 --seconds 90 --frame-ms 100 --blocksize-frames 0 --timeout 300
```

The server emits `turn_started`, `utterance_saved`, `asr_result`, `llm_result`, `tts_result`, and `reply_audio` events. Reply WAV files are saved under `outputs/stream_replies/` on Windows and `outputs/ws_stream_tts_replies/` on the server.

This first version is logically half-duplex: after the server accepts an utterance, the client pauses upstream microphone frames until reply playback completes. With WASAPI input/output endpoints, `--audio-mode auto` keeps one full-duplex device Stream open; it does not reopen a second PortAudio output stream for every reply. Non-WASAPI device pairs fall back to the older separate-stream path. The client resumes immediately when ASR/LLM/TTS produces no reply. Use headphones for the cleanest initial validation.

This full loop was validated with a 48 kHz HP21 WASAPI microphone resampled continuously to 16 kHz, Silero VAD, sherpa-onnx SenseVoice, Qwen3-8B through Ollama, and CosyVoice SFT. Device IDs are assigned dynamically by Windows; use `--list-devices` rather than assuming the example IDs remain stable.

To explicitly require the new HP21 single-Stream path during validation, add `--audio-mode duplex_wasapi --duplex-latency high` to the streaming client command. `auto` selects it when both selected devices belong to Windows WASAPI.

## Experimental WASAPI Duplex Stream Test

The next audio-I/O step keeps one WASAPI full-duplex stream open for HP21 microphone capture and speaker playback. This test does not connect to the server; it validates the device layer before the WebSocket client is changed.

Use an existing reply WAV from `outputs/stream_replies/` as `--play-wav`:

```powershell
python -m auralis_client.duplex_stream_test --input-device 21 --output-device 16 --play-wav outputs/stream_replies/<reply-file>.wav --seconds 30 --frame-ms 100 --blocksize-frames 0 --latency high
```

The test keeps a single `sounddevice.Stream` open at a shared device rate, normally 48 kHz for HP21. It continuously saves channel-0 microphone audio as 16 kHz mono PCM16 under `outputs/duplex-stream-input-16k.wav`, while it injects the selected reply WAV through the same Stream's output side. Successful hardware validation requires clean simultaneous capture and playback with no PortAudio error.
