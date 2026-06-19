from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import soundfile as sf

from auralis_client.audio_io import choose_devices_interactively, play_wav, record_first_channel_16k, save_wav_16k
from auralis_client.config import CLIENT_SAMPLE_RATE


async def upload_wav(
    server_url: str,
    wav_path: str,
    timeout: float,
    expect_reply_audio: bool,
    reply_output: str,
    output_device: int | None,
) -> None:
    try:
        websockets = __import__("websockets")
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing dependency: websockets\nInstall it with:\n  python -m pip install websockets") from exc

    path = Path(wav_path)
    if not path.exists():
        raise SystemExit(f"WAV file was not found: {wav_path}")

    info = sf.info(str(path))
    audio_bytes = path.read_bytes()
    metadata = {
        "type": "audio_upload",
        "client": "AuralisClient",
        "format": "wav",
        "suffix": ".wav",
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "duration_seconds": float(info.duration),
        "bytes": len(audio_bytes),
        "filename": path.name,
        "client_time": time.time(),
    }

    started = time.perf_counter()
    async with websockets.connect(server_url, open_timeout=timeout, max_size=None) as websocket:
        await websocket.send(json.dumps(metadata, ensure_ascii=False))
        ready = await asyncio.wait_for(websocket.recv(), timeout=timeout)
        print("SERVER_READY:")
        print(ready)
        await websocket.send(audio_bytes)
        ack = await asyncio.wait_for(websocket.recv(), timeout=timeout)
        reply_meta = None
        reply_audio_bytes = None
        if expect_reply_audio:
            reply_meta = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            parsed_reply_meta = json.loads(reply_meta)
            if parsed_reply_meta.get("type") == "reply_audio_error":
                raise SystemExit(parsed_reply_meta.get("message", "Server failed to prepare reply audio."))
            if parsed_reply_meta.get("type") == "pipeline_error":
                raise SystemExit(parsed_reply_meta.get("message", "Server pipeline failed."))
            if parsed_reply_meta.get("type") != "reply_audio":
                raise SystemExit(f"Expected reply_audio metadata, got: {reply_meta}")
            reply_audio_bytes = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    elapsed_ms = (time.perf_counter() - started) * 1000

    print(f"SERVER_URL: {server_url}")
    print(f"UPLOAD_ROUND_TRIP_MS: {elapsed_ms:.1f}")
    print("UPLOAD_ACK:")
    print(ack)

    if expect_reply_audio and reply_meta is not None and reply_audio_bytes is not None:
        reply_path = Path(reply_output)
        reply_path.parent.mkdir(parents=True, exist_ok=True)
        reply_path.write_bytes(reply_audio_bytes)
        print("REPLY_AUDIO_META:")
        print(reply_meta)
        print(f"REPLY_AUDIO_OUTPUT: {reply_path}")
        if output_device is not None:
            print("Playing reply audio...")
            play_wav(reply_path, output_device)
            print("Done.")


def record_temp_wav(input_device: int, seconds: float, output_dir: str) -> str:
    audio = record_first_channel_16k(input_device, seconds)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    wav_path = output_path / f"upload-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    save_wav_16k(wav_path, audio)
    return str(wav_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a 16 kHz mono wav to the Auralis server over WebSocket.")
    parser.add_argument("--server-url", default="ws://192.168.16.206:8765")
    parser.add_argument("--wav", default=None, help="Existing wav file to upload.")
    parser.add_argument("--record-seconds", type=float, default=None, help="Record a temporary wav before upload.")
    parser.add_argument("--record-output-dir", default="outputs/uploads", help="Where recorded upload wav files are stored.")
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--expect-reply-audio", action="store_true")
    parser.add_argument("--reply-output", default="outputs/server-reply.wav")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    wav_path = args.wav
    if wav_path is None:
        seconds = args.record_seconds if args.record_seconds is not None else 3.0
        if args.input_device is None:
            _, input_device, output_device = choose_devices_interactively()
            if args.output_device is None:
                args.output_device = output_device
        else:
            input_device = args.input_device
        print(f"Recording {seconds:.1f} seconds before upload...")
        wav_path = record_temp_wav(input_device, seconds, args.record_output_dir)
        print(f"Recorded wav: {wav_path}")

    info = sf.info(str(wav_path))
    if int(info.samplerate) != CLIENT_SAMPLE_RATE or int(info.channels) != 1:
        print(
            "Warning: uploaded wav is not 16 kHz mono. "
            f"Got {info.samplerate} Hz, {info.channels} channel(s)."
        )

    asyncio.run(
        upload_wav(
            args.server_url,
            wav_path,
            args.timeout,
            expect_reply_audio=args.expect_reply_audio,
            reply_output=args.reply_output,
            output_device=args.output_device,
        )
    )


if __name__ == "__main__":
    main()
