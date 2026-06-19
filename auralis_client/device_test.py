from __future__ import annotations

import argparse

from auralis_client.audio_io import choose_devices_interactively, play_wav, print_devices, record_first_channel_16k, save_wav_16k


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate local microphone capture and speaker playback.")
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--output", default="outputs/device-test.wav")
    parser.add_argument("--list-devices", action="store_true", help="Print devices grouped by host API and exit.")
    parser.add_argument("--input-device", type=int, default=None, help="PortAudio microphone device id.")
    parser.add_argument("--output-device", type=int, default=None, help="PortAudio speaker device id.")
    args = parser.parse_args()

    if args.list_devices:
        print_devices()
        return

    if args.input_device is not None and args.output_device is not None:
        input_device = args.input_device
        output_device = args.output_device
    else:
        _, input_device, output_device = choose_devices_interactively()

    print(f"Recording {args.seconds:.1f} seconds...")
    audio = record_first_channel_16k(input_device, args.seconds)
    save_wav_16k(args.output, audio)
    print(f"Saved: {args.output}")

    print("Playing recorded wav...")
    play_wav(args.output, output_device)
    print("Done.")


if __name__ == "__main__":
    main()
