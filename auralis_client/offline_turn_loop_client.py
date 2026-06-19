from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import soundfile as sf

from auralis_client.audio_io import choose_devices_interactively, play_wav, record_first_channel_16k, save_wav_16k
from auralis_client.config import CLIENT_SAMPLE_RATE


def record_turn_wav(input_device: int, seconds: float, output_dir: str, turn_index: int) -> str:
    audio = record_first_channel_16k(input_device, seconds)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    wav_path = output_path / f"turn-input-{turn_index:03d}-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    save_wav_16k(wav_path, audio)
    return str(wav_path)


def build_upload_metadata(wav_path: str, turn_index: int) -> dict:
    path = Path(wav_path)
    info = sf.info(str(path))
    return {
        "type": "audio_upload",
        "client": "AuralisClient",
        "turn_index": turn_index,
        "format": "wav",
        "suffix": ".wav",
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "duration_seconds": float(info.duration),
        "bytes": path.stat().st_size,
        "filename": path.name,
        "client_time": time.time(),
    }


async def send_turn(
    websocket,
    wav_path: str,
    turn_index: int,
    timeout: float,
    reply_output_dir: str,
    output_device: int,
) -> None:
    metadata = build_upload_metadata(wav_path, turn_index)
    if metadata["sample_rate"] != CLIENT_SAMPLE_RATE or metadata["channels"] != 1:
        print(
            "Warning: uploaded wav is not 16 kHz mono. "
            f"Got {metadata['sample_rate']} Hz, {metadata['channels']} channel(s)."
        )

    started = time.perf_counter()
    await websocket.send(json.dumps(metadata, ensure_ascii=False))
    ready = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    print("SERVER_READY:")
    print(ready)

    await websocket.send(Path(wav_path).read_bytes())
    ack = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    print("UPLOAD_ACK:")
    print(ack)

    reply_meta = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    parsed_reply_meta = json.loads(reply_meta)
    if parsed_reply_meta.get("type") == "pipeline_error":
        raise RuntimeError(parsed_reply_meta.get("message", "Server pipeline failed."))
    if parsed_reply_meta.get("type") != "reply_audio":
        raise RuntimeError(f"Expected reply_audio metadata, got: {reply_meta}")

    reply_audio_bytes = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    reply_output = Path(reply_output_dir)
    reply_output.mkdir(parents=True, exist_ok=True)
    reply_path = reply_output / f"turn-reply-{turn_index:03d}.wav"
    reply_path.write_bytes(reply_audio_bytes)

    elapsed_ms = (time.perf_counter() - started) * 1000
    print("REPLY_AUDIO_META:")
    print(reply_meta)
    print(f"REPLY_AUDIO_OUTPUT: {reply_path}")
    print(f"TURN_ROUND_TRIP_MS: {elapsed_ms:.1f}")

    print("Playing reply audio...")
    play_wav(reply_path, output_device)
    print("Playback done.")


async def run_loop(args: argparse.Namespace) -> None:
    try:
        websockets = __import__("websockets")
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing dependency: websockets\nInstall it with:\n  python -m pip install websockets") from exc

    input_device = args.input_device
    output_device = args.output_device
    if input_device is None or output_device is None:
        _, selected_input, selected_output = choose_devices_interactively()
        input_device = selected_input if input_device is None else input_device
        output_device = selected_output if output_device is None else output_device

    print(f"Connecting to {args.server_url} ...")
    async with websockets.connect(args.server_url, open_timeout=args.timeout, max_size=None) as websocket:
        print("Connected. Press Ctrl+C to stop after any turn.")
        turn_index = 1
        while True:
            print()
            print(f"TURN {turn_index}: recording {args.record_seconds:.1f} seconds...")
            wav_path = record_turn_wav(input_device, args.record_seconds, args.record_output_dir, turn_index)
            print(f"Recorded wav: {wav_path}")
            await send_turn(
                websocket,
                wav_path=wav_path,
                turn_index=turn_index,
                timeout=args.timeout,
                reply_output_dir=args.reply_output_dir,
                output_device=output_device,
            )
            turn_index += 1
            print(f"Ready for next turn. Please speak within the next {args.record_seconds:.1f} seconds.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run repeated offline Auralis client/server voice turns.")
    parser.add_argument("--server-url", default="ws://192.168.16.206:8765")
    parser.add_argument("--record-seconds", type=float, default=5.0)
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--record-output-dir", default="outputs/turn_inputs")
    parser.add_argument("--reply-output-dir", default="outputs/turn_replies")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    try:
        asyncio.run(run_loop(args))
    except KeyboardInterrupt:
        print()
        print("Stopped by user.")


if __name__ == "__main__":
    main()
