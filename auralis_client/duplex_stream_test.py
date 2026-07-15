from __future__ import annotations

import argparse
import time
from queue import Empty, Queue

import numpy as np

from auralis_client.audio_io import DuplexAudioEngine, choose_devices_interactively, save_wav_16k
from auralis_client.config import CLIENT_SAMPLE_RATE


def resolve_devices(input_device: int | None, output_device: int | None) -> tuple[int, int]:
    if input_device is not None and output_device is not None:
        return input_device, output_device
    _, selected_input, selected_output = choose_devices_interactively()
    return (
        selected_input if input_device is None else input_device,
        selected_output if output_device is None else output_device,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate one WASAPI full-duplex audio stream.")
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--play-wav", required=True, help="WAV file to inject into the duplex playback queue.")
    parser.add_argument("--output", default="outputs/duplex-stream-input-16k.wav")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--repeat-gap-seconds", type=float, default=1.0)
    parser.add_argument("--frame-ms", type=int, default=100)
    parser.add_argument("--blocksize-frames", type=int, default=0)
    parser.add_argument("--latency", choices=["low", "high"], default="high")
    args = parser.parse_args()

    input_device, output_device = resolve_devices(args.input_device, args.output_device)
    input_frames: Queue[bytes] = Queue()
    engine = DuplexAudioEngine(
        input_device=input_device,
        output_device=output_device,
        input_frame_callback=input_frames.put,
        frame_duration_ms=args.frame_ms,
        blocksize_frames=args.blocksize_frames,
        latency=args.latency,
    )

    pcm16 = bytearray()
    completions = []
    next_playback = time.perf_counter()
    remaining_plays = max(0, args.repeat)
    deadline = time.perf_counter() + args.seconds
    engine.start()
    print("Duplex stream running. Press Ctrl+C to stop early.")
    try:
        while time.perf_counter() < deadline:
            now = time.perf_counter()
            if remaining_plays and now >= next_playback:
                completions.append(engine.enqueue_wav(args.play_wav))
                remaining_plays -= 1
                next_playback = now + args.repeat_gap_seconds
                print(f"PLAYBACK_QUEUED: remaining {remaining_plays}")
            try:
                pcm16.extend(input_frames.get(timeout=0.05))
            except Empty:
                pass
            for status in engine.drain_status():
                print(status)
    except KeyboardInterrupt:
        print()
        print("Stopping duplex stream by user request...")
    finally:
        completed_before_stop = sum(event.is_set() for event in completions)
        engine.stop()

    if not pcm16:
        print("No microphone PCM16 frames were captured.")
        return
    audio = np.frombuffer(bytes(pcm16), dtype="<i2").astype("float32") / 32767.0
    save_wav_16k(args.output, audio)
    print(f"INPUT_WAV: {args.output}")
    print(f"INPUT_SECONDS: {audio.size / CLIENT_SAMPLE_RATE:.3f}")
    print(f"PLAYBACKS_COMPLETED_BEFORE_STOP: {completed_before_stop}/{len(completions)}")


if __name__ == "__main__":
    main()
