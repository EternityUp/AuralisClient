from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from typing import Awaitable, Callable

import sounddevice as sd

from auralis_client.audio_io import (
    DuplexAudioEngine,
    choose_devices_interactively,
    create_first_channel_pcm16_input_stream,
    play_wav,
)
from auralis_client.config import CLIENT_SAMPLE_RATE


def clear_frame_queue(frame_queue: Queue[bytes]) -> None:
    while True:
        try:
            frame_queue.get_nowait()
        except Empty:
            return


def is_wasapi_duplex_pair(input_device: int, output_device: int) -> bool:
    input_info = sd.query_devices(input_device, "input")
    output_info = sd.query_devices(output_device, "output")
    input_hostapi = sd.query_hostapis(int(input_info["hostapi"]))
    output_hostapi = sd.query_hostapis(int(output_info["hostapi"]))
    return input_hostapi["name"] == "Windows WASAPI" and output_hostapi["name"] == "Windows WASAPI"


async def handle_server_message(
    websocket,
    message: str | bytes,
    args: argparse.Namespace,
    frame_queue: Queue[bytes],
    server_events: list[str],
    pause_uplink: Callable[[], None],
    resume_uplink: Callable[[], None],
    play_reply_audio: Callable[[Path], Awaitable[None]],
) -> str | None:
    if isinstance(message, bytes):
        print(f"Unexpected binary server message: {len(message)} bytes")
        return None

    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        payload = {"type": "text"}
    message_type = payload.get("type")

    if message_type == "turn_started":
        pause_uplink()
        clear_frame_queue(frame_queue)
        print("Server accepted an utterance; upstream microphone frames paused for this reply.")
    elif message_type in {"asr_filtered", "llm_error", "llm_filtered", "tts_error"}:
        # No reply audio will follow, so allow the next utterance immediately.
        clear_frame_queue(frame_queue)
        resume_uplink()
        print("No reply audio for this utterance; microphone uplink resumed.")

    if message_type != "reply_audio":
        server_events.append(message)
        print("SERVER_EVENT:")
        print(message)
        return message_type

    audio_bytes = await asyncio.wait_for(websocket.recv(), timeout=args.timeout)
    if not isinstance(audio_bytes, bytes):
        raise RuntimeError("Expected reply WAV bytes after reply_audio metadata.")

    filename = Path(str(payload.get("filename", "reply.wav"))).name
    reply_path = Path(args.reply_output_dir) / filename
    reply_path.parent.mkdir(parents=True, exist_ok=True)
    reply_path.write_bytes(audio_bytes)
    server_events.append(message)
    print("REPLY_AUDIO_META:")
    print(message)
    print(f"REPLY_AUDIO_OUTPUT: {reply_path}")

    pause_uplink()
    try:
        await play_reply_audio(reply_path)
    finally:
        clear_frame_queue(frame_queue)
        resume_uplink()
    print("Reply playback done.")
    return message_type


async def run_stream_upload(args: argparse.Namespace) -> None:
    try:
        websockets = __import__("websockets")
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing dependency: websockets\nInstall it with:\n  python -m pip install websockets") from exc

    input_device = args.input_device
    output_device = args.output_device
    if input_device is None:
        _, selected_input, selected_output = choose_devices_interactively()
        input_device = selected_input
        if output_device is None:
            output_device = selected_output
    args.output_device = output_device

    if args.audio_mode == "duplex_wasapi":
        if output_device is None:
            raise SystemExit("--audio-mode duplex_wasapi requires --output-device.")
        use_duplex = True
    elif args.audio_mode == "auto":
        use_duplex = output_device is not None and is_wasapi_duplex_pair(input_device, output_device)
    else:
        use_duplex = False
    if use_duplex:
        print("AUDIO_MODE: duplex_wasapi")
    else:
        print("AUDIO_MODE: separate_streams")

    frame_queue: Queue[bytes] = Queue()
    uplink_paused = Event()

    def on_frame(frame: bytes) -> None:
        if not uplink_paused.is_set():
            frame_queue.put(frame)

    metadata = {
        "type": "stream_start",
        "client": "AuralisClient",
        "format": "pcm_s16le",
        "sample_rate": CLIENT_SAMPLE_RATE,
        "channels": 1,
        "frame_ms": args.frame_ms,
        "client_time": time.time(),
    }

    frame_count = 0
    byte_count = 0
    server_events: list[str] = []
    started = time.perf_counter()
    capture_stream = None
    duplex_engine: DuplexAudioEngine | None = None
    capture_resumable = True

    if use_duplex:
        assert output_device is not None
        duplex_engine = DuplexAudioEngine(
            input_device=input_device,
            output_device=output_device,
            input_frame_callback=on_frame,
            frame_duration_ms=args.frame_ms,
            blocksize_frames=args.blocksize_frames,
            latency=args.duplex_latency,
        )

    def start_capture() -> None:
        nonlocal capture_stream
        if not capture_resumable:
            return
        if duplex_engine is not None:
            duplex_engine.start()
            return
        if capture_stream is not None:
            return
        stream = create_first_channel_pcm16_input_stream(
            input_device,
            on_frame,
            args.frame_ms,
            args.blocksize_frames,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        capture_stream = stream

    def stop_capture() -> None:
        nonlocal capture_stream
        if duplex_engine is not None:
            duplex_engine.stop()
            return
        if capture_stream is None:
            return
        try:
            capture_stream.stop()
        finally:
            capture_stream.close()
            capture_stream = None

    def pause_uplink() -> None:
        uplink_paused.set()
        clear_frame_queue(frame_queue)
        if duplex_engine is None:
            stop_capture()

    def resume_uplink() -> None:
        clear_frame_queue(frame_queue)
        if not capture_resumable:
            return
        uplink_paused.clear()
        if duplex_engine is None:
            start_capture()

    async def play_reply_audio(reply_path: Path) -> None:
        if duplex_engine is None:
            if output_device is None:
                return
            print("Playing reply audio through a separate output stream...")
            # Keep Windows WASAPI stream creation on the main client thread.
            play_wav(reply_path, output_device)
            return

        print("Playing reply audio through the persistent duplex stream...")
        completion = duplex_engine.enqueue_wav(reply_path)
        await asyncio.wait_for(asyncio.to_thread(completion.wait), timeout=args.timeout)
        for status in duplex_engine.drain_status():
            print(status)

    async with websockets.connect(args.server_url, open_timeout=args.timeout, max_size=None) as websocket:
        await websocket.send(json.dumps(metadata, ensure_ascii=False))
        ready = await asyncio.wait_for(websocket.recv(), timeout=args.timeout)
        print("SERVER_READY:")
        print(ready)

        print(f"Streaming for {args.seconds:.1f} seconds. Press Ctrl+C to stop early.")
        end_time = time.perf_counter() + args.seconds
        try:
            start_capture()
            while time.perf_counter() < end_time:
                try:
                    frame = frame_queue.get(timeout=0.2)
                except Empty:
                    # During reply generation/playback the duplex stream stays
                    # open, but its input frames are intentionally not sent.
                    try:
                        event = await asyncio.wait_for(websocket.recv(), timeout=0.05)
                    except asyncio.TimeoutError:
                        if duplex_engine is not None:
                            for status in duplex_engine.drain_status():
                                print(status)
                        await asyncio.sleep(0)
                    else:
                        await handle_server_message(
                            websocket,
                            event,
                            args,
                            frame_queue,
                            server_events,
                            pause_uplink,
                            resume_uplink,
                            play_reply_audio,
                        )
                    continue
                await websocket.send(frame)
                frame_count += 1
                byte_count += len(frame)
                while True:
                    try:
                        event = await asyncio.wait_for(websocket.recv(), timeout=0.001)
                    except asyncio.TimeoutError:
                        break
                    await handle_server_message(
                        websocket,
                        event,
                        args,
                        frame_queue,
                        server_events,
                        pause_uplink,
                        resume_uplink,
                        play_reply_audio,
                    )
        except KeyboardInterrupt:
            print()
            print("Stopping stream by user request...")
        finally:
            capture_resumable = False
            uplink_paused.set()
            clear_frame_queue(frame_queue)
            if duplex_engine is None:
                stop_capture()

        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "stream_stop",
                        "client_time": time.time(),
                        "frames": frame_count,
                        "bytes": byte_count,
                    },
                    ensure_ascii=False,
                )
            )
            ack = None
            while ack is None:
                event = await asyncio.wait_for(websocket.recv(), timeout=args.timeout)
                if isinstance(event, str):
                    try:
                        event_type = json.loads(event).get("type")
                    except Exception:
                        event_type = None
                else:
                    event_type = None
                if event_type in (
                    "stream_saved",
                    "stream_vad_stopped",
                    "stream_asr_stopped",
                    "stream_llm_stopped",
                    "stream_tts_stopped",
                ):
                    ack = event
                else:
                    await handle_server_message(
                        websocket,
                        event,
                        args,
                        frame_queue,
                        server_events,
                        pause_uplink,
                        resume_uplink,
                        play_reply_audio,
                    )
            while True:
                try:
                    event = await asyncio.wait_for(websocket.recv(), timeout=0.05)
                except asyncio.TimeoutError:
                    break
                await handle_server_message(
                    websocket,
                    event,
                    args,
                    frame_queue,
                    server_events,
                    pause_uplink,
                    resume_uplink,
                    play_reply_audio,
                )
        finally:
            stop_capture()

    elapsed = time.perf_counter() - started
    print("STREAM_ACK:")
    print(ack)
    print(f"CLIENT_FRAMES_SENT: {frame_count}")
    print(f"CLIENT_BYTES_SENT: {byte_count}")
    print(f"CLIENT_STREAM_SECONDS: {elapsed:.3f}")
    print(f"SERVER_EVENTS_RECEIVED: {len(server_events)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream 16 kHz mono PCM16 microphone frames to the Auralis server.")
    parser.add_argument("--server-url", default="ws://192.168.16.206:8766")
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--reply-output-dir", default="outputs/stream_replies")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--frame-ms", type=int, default=100)
    parser.add_argument(
        "--blocksize-frames",
        type=int,
        default=None,
        help="Audio stream blocksize in frames. Use 0 to let PortAudio choose automatically.",
    )
    parser.add_argument(
        "--audio-mode",
        choices=["auto", "duplex_wasapi", "separate_streams"],
        default="auto",
        help="auto uses one WASAPI duplex stream when both selected devices are WASAPI endpoints.",
    )
    parser.add_argument(
        "--duplex-latency",
        choices=["low", "high"],
        default="high",
        help="Requested PortAudio latency for the WASAPI duplex stream.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    asyncio.run(run_stream_upload(args))


if __name__ == "__main__":
    main()
