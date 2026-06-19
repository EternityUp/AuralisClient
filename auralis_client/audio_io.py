from __future__ import annotations

from pathlib import Path
from math import gcd
from queue import Empty, Queue
from threading import Event

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy import signal

from auralis_client.config import CLIENT_SAMPLE_RATE


def query_devices() -> list[dict]:
    return list(sd.query_devices())


def query_hostapis() -> list[dict]:
    return list(sd.query_hostapis())


def clean_device_name(name: object) -> str:
    return " ".join(str(name).split())


def device_line(index: int, device: dict, direction: str) -> str:
    channels_key = "max_input_channels" if direction == "input" else "max_output_channels"
    channels_label = "in" if direction == "input" else "out"
    return (
        f"  [{index}] {clean_device_name(device['name'])} "
        f"({device[channels_key]} {channels_label}, default {device['default_samplerate']} Hz)"
    )


def device_matches_hostapi(device: dict, hostapi_index: int) -> bool:
    return int(device["hostapi"]) == hostapi_index


def input_devices_for_hostapi(hostapi_index: int) -> list[tuple[int, dict]]:
    return [
        (index, device)
        for index, device in enumerate(query_devices())
        if device_matches_hostapi(device, hostapi_index) and int(device["max_input_channels"]) > 0
    ]


def output_devices_for_hostapi(hostapi_index: int) -> list[tuple[int, dict]]:
    return [
        (index, device)
        for index, device in enumerate(query_devices())
        if device_matches_hostapi(device, hostapi_index) and int(device["max_output_channels"]) > 0
    ]


def print_hostapis() -> None:
    hostapis = query_hostapis()
    print("Audio host APIs:")
    for index, hostapi in enumerate(hostapis):
        inputs = input_devices_for_hostapi(index)
        outputs = output_devices_for_hostapi(index)
        default_input = hostapi.get("default_input_device", -1)
        default_output = hostapi.get("default_output_device", -1)
        print(
            f"  [{index}] {hostapi['name']} "
            f"({len(inputs)} input, {len(outputs)} output, "
            f"default input {default_input}, default output {default_output})"
        )


def print_devices_for_hostapi(hostapi_index: int) -> None:
    hostapi = query_hostapis()[hostapi_index]
    print(f"Host API: [{hostapi_index}] {hostapi['name']}")
    print()
    print("Input devices:")
    inputs = input_devices_for_hostapi(hostapi_index)
    if not inputs:
        print("  No input devices.")
    for index, device in inputs:
        print(device_line(index, device, "input"))

    print()
    print("Output devices:")
    outputs = output_devices_for_hostapi(hostapi_index)
    if not outputs:
        print("  No output devices.")
    for index, device in outputs:
        print(device_line(index, device, "output"))


def print_devices() -> None:
    hostapis = query_hostapis()
    for hostapi_index, hostapi in enumerate(hostapis):
        inputs = input_devices_for_hostapi(hostapi_index)
        outputs = output_devices_for_hostapi(hostapi_index)
        if not inputs and not outputs:
            continue

        print("=" * 72)
        print(f"Host API [{hostapi_index}]: {hostapi['name']}")
        print("Input devices:")
        if not inputs:
            print("  No input devices.")
        for index, device in inputs:
            print(device_line(index, device, "input"))

        print("Output devices:")
        if not outputs:
            print("  No output devices.")
        for index, device in outputs:
            print(device_line(index, device, "output"))
    print("=" * 72)


def choose_hostapi() -> int:
    print_hostapis()
    while True:
        raw = input("Select audio host API id: ").strip()
        try:
            hostapi_index = int(raw)
        except ValueError:
            print("Please enter a numeric host API id.")
            continue
        if 0 <= hostapi_index < len(query_hostapis()):
            return hostapi_index
        print("Host API id is out of range.")


def choose_device(hostapi_index: int, direction: str) -> int:
    if direction == "input":
        candidates = dict(input_devices_for_hostapi(hostapi_index))
        prompt = "Select microphone device id: "
    elif direction == "output":
        candidates = dict(output_devices_for_hostapi(hostapi_index))
        prompt = "Select speaker device id: "
    else:
        raise ValueError(f"Unsupported device direction: {direction}")

    while True:
        raw = input(prompt).strip()
        try:
            device_index = int(raw)
        except ValueError:
            print("Please enter a numeric device id.")
            continue
        if device_index in candidates:
            return device_index
        print("Device id is not available under the selected host API.")


def choose_devices_interactively() -> tuple[int, int, int]:
    hostapi_index = choose_hostapi()
    print()
    print_devices_for_hostapi(hostapi_index)
    print()
    input_device = choose_device(hostapi_index, "input")
    output_device = choose_device(hostapi_index, "output")
    return hostapi_index, input_device, output_device


def record_first_channel_16k(device: int, seconds: float) -> np.ndarray:
    device_info = sd.query_devices(device, "input")
    source_sample_rate = choose_capture_sample_rate(device, int(device_info["default_samplerate"]), int(device_info["max_input_channels"]))
    frames = int(source_sample_rate * seconds)
    audio = sd.rec(frames, samplerate=source_sample_rate, channels=int(device_info["max_input_channels"]), dtype="float32", device=device)
    sd.wait()

    if audio.ndim > 1:
        audio = audio[:, 0]
    return resample_audio(audio, source_sample_rate, CLIENT_SAMPLE_RATE)


def choose_capture_sample_rate(device: int, default_sample_rate: int, channels: int) -> int:
    candidates = unique_sample_rates([CLIENT_SAMPLE_RATE, default_sample_rate, 48000, 44100, 32000, 24000])
    last_error: Exception | None = None
    for sample_rate in candidates:
        try:
            sd.check_input_settings(device=device, samplerate=sample_rate, channels=channels, dtype="float32")
            return sample_rate
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        f"No supported capture sample rate found for input device {device}. "
        f"Tried: {candidates}. Last error: {last_error}"
    )


def capture_first_channel_16k_stream(
    device: int,
    stop_event: Event,
    block_duration_ms: int = 100,
) -> list[np.ndarray]:
    device_info = sd.query_devices(device, "input")
    source_channels = int(device_info["max_input_channels"])
    source_sample_rate = choose_capture_sample_rate(device, int(device_info["default_samplerate"]), source_channels)
    blocksize = max(1, int(source_sample_rate * block_duration_ms / 1000))
    source_blocks: list[np.ndarray] = []
    queue: Queue[np.ndarray] = Queue()

    def callback(indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"Input stream status: {status}")
        block = indata.copy()
        if block.ndim > 1:
            block = block[:, 0]
        queue.put(block)

    with sd.InputStream(
        samplerate=source_sample_rate,
        blocksize=blocksize,
        device=device,
        channels=source_channels,
        dtype="float32",
        callback=callback,
    ):
        print(
            f"Capturing from device {device}: "
            f"{source_sample_rate} Hz, {source_channels} channel(s), "
            f"{block_duration_ms} ms blocks -> {CLIENT_SAMPLE_RATE} Hz mono"
        )
        try:
            while not stop_event.is_set():
                try:
                    source_blocks.append(queue.get(timeout=0.2))
                except Empty:
                    continue
        except KeyboardInterrupt:
            stop_event.set()
            print()
            print("Stopping capture...")

    while True:
        try:
            source_blocks.append(queue.get_nowait())
        except Empty:
            break

    if not source_blocks:
        return []

    source_audio = concatenate_audio_blocks(source_blocks)
    audio_16k = resample_audio(source_audio, source_sample_rate, CLIENT_SAMPLE_RATE)
    return [audio_16k]


def concatenate_audio_blocks(blocks: list[np.ndarray]) -> np.ndarray:
    if not blocks:
        return np.zeros(0, dtype="float32")
    return np.concatenate(blocks).astype("float32")


def save_wav_16k(path: str | Path, audio: np.ndarray) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), audio, CLIENT_SAMPLE_RATE)


def resample_audio(audio: np.ndarray, source_sample_rate: int, target_sample_rate: int) -> np.ndarray:
    if source_sample_rate == target_sample_rate:
        return np.asarray(audio, dtype="float32")
    divisor = gcd(source_sample_rate, target_sample_rate)
    up = target_sample_rate // divisor
    down = source_sample_rate // divisor
    return signal.resample_poly(audio, up, down).astype("float32")


def unique_sample_rates(sample_rates: list[int]) -> list[int]:
    seen = set()
    result = []
    for sample_rate in sample_rates:
        if sample_rate <= 0 or sample_rate in seen:
            continue
        seen.add(sample_rate)
        result.append(sample_rate)
    return result


def choose_playback_sample_rate(device: int | None, source_sample_rate: int, channels: int) -> int:
    if device is None:
        return source_sample_rate

    device_info = sd.query_devices(device, "output")
    default_sample_rate = int(device_info["default_samplerate"])
    candidates = unique_sample_rates([default_sample_rate, source_sample_rate, 48000, 44100, 32000, 24000, 16000])
    last_error: Exception | None = None
    for sample_rate in candidates:
        try:
            sd.check_output_settings(device=device, samplerate=sample_rate, channels=channels, dtype="float32")
            return sample_rate
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        f"No supported playback sample rate found for output device {device}. "
        f"Tried: {candidates}. Last error: {last_error}"
    )


def add_playback_guard_silence(
    audio: np.ndarray,
    sample_rate: int,
    head_ms: int = 1000,
    tail_ms: int = 300,
) -> np.ndarray:
    if audio.ndim == 1:
        head_shape = (int(sample_rate * head_ms / 1000),)
        tail_shape = (int(sample_rate * tail_ms / 1000),)
    else:
        channels = audio.shape[1]
        head_shape = (int(sample_rate * head_ms / 1000), channels)
        tail_shape = (int(sample_rate * tail_ms / 1000), channels)
    head = np.zeros(head_shape, dtype="float32")
    tail = np.zeros(tail_shape, dtype="float32")
    return np.concatenate([head, audio.astype("float32"), tail])


def play_wav(
    path: str | Path,
    device: int | None = None,
    head_silence_ms: int = 1000,
    tail_silence_ms: int = 300,
) -> None:
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    channels = 1 if audio.ndim == 1 else int(audio.shape[1])
    playback_sample_rate = choose_playback_sample_rate(device, int(sample_rate), channels)
    audio = resample_audio(audio, int(sample_rate), playback_sample_rate)
    audio = add_playback_guard_silence(audio, playback_sample_rate, head_silence_ms, tail_silence_ms)
    if playback_sample_rate != int(sample_rate):
        print(f"Playback resampled: {int(sample_rate)} Hz -> {playback_sample_rate} Hz")
    if head_silence_ms or tail_silence_ms:
        print(f"Playback guard silence: head {head_silence_ms} ms, tail {tail_silence_ms} ms")
    sd.play(audio, samplerate=playback_sample_rate, device=device)
    sd.wait()
