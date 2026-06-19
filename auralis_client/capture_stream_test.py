from __future__ import annotations

import argparse
from threading import Event

from auralis_client.audio_io import (
    capture_first_channel_16k_stream,
    choose_devices_interactively,
    concatenate_audio_blocks,
    play_wav,
    save_wav_16k,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate continuous microphone capture as 16 kHz mono audio.")
    parser.add_argument("--output", default="outputs/capture-stream-test.wav")
    parser.add_argument("--block-ms", type=int, default=100)
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--no-playback", action="store_true")
    args = parser.parse_args()

    if args.input_device is not None:
        input_device = args.input_device
        output_device = args.output_device
    else:
        _, input_device, output_device = choose_devices_interactively()

    stop_event = Event()
    print("Start continuous capture. Press Ctrl+C to stop and save.")
    blocks = capture_first_channel_16k_stream(
        input_device,
        stop_event=stop_event,
        block_duration_ms=args.block_ms,
    )

    audio = concatenate_audio_blocks(blocks)
    if audio.size == 0:
        print("No audio was captured.")
        return

    duration = audio.size / 16000
    save_wav_16k(args.output, audio)
    print(f"Saved: {args.output}")
    print(f"Captured duration: {duration:.2f} seconds")

    if not args.no_playback and output_device is not None:
        print("Playing captured wav...")
        play_wav(args.output, output_device)
        print("Done.")


if __name__ == "__main__":
    main()
