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
