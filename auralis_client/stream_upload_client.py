from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from typing import Callable

from auralis_client.audio_io import choose_devices_interactively, create_first_channel_pcm16_input_stream, play_wav
from auralis_client.config import CLIENT_SAMPLE_RATE


def clear_frame_queue(frame_queue: Queue[bytes]) -> None:
    while True:
        try:
            frame_queue.get_nowait()
        except Empty:
            return


async def handle_server_message(
    websocket,
    message: str | bytes,
    args: argparse.Namespace,
    frame_queue: Queue[bytes],
    capture_paused: Event,
    server_events: list[str],
    stop_capture: Callable[[], None],
    start_capture: Callable[[], None],
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
        capture_paused.set()
        clear_frame_queue(frame_queue)
        stop_capture()
        print("Server accepted an utterance; microphone capture paused for this reply.")
    elif message_type in {"asr_filtered", "llm_error", "llm_filtered", "tts_error"}:
        # No reply audio will follow, so allow the next utterance immediately.
        clear_frame_queue(frame_queue)
        capture_paused.clear()
        start_capture()
        print("No reply audio for this utterance; microphone capture resumed.")

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

    if args.output_device is not None:
        capture_paused.set()
        clear_frame_queue(frame_queue)
        print("Playing reply audio...")
        try:
            # Keep Windows WASAPI stream creation on the main client thread.
            # The offline client uses the same synchronous path and some USB
            # drivers fail when PortAudio opens their output endpoint from an
            # asyncio worker thread.
            play_wav(reply_path, args.output_device)
        finally:
            clear_frame_queue(frame_queue)
            capture_paused.clear()
            start_capture()
        print("Reply playback done.")
    else:
        clear_frame_queue(frame_queue)
        capture_paused.clear()
        start_capture()
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

    frame_queue: Queue[bytes] = Queue()
    capture_paused = Event()

    def on_frame(frame: bytes) -> None:
        if not capture_paused.is_set():
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
    capture_resumable = True

    def start_capture() -> None:
        nonlocal capture_stream
        if not capture_resumable or capture_stream is not None:
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
        if capture_stream is None:
            return
        try:
            capture_stream.stop()
        finally:
            capture_stream.close()
            capture_stream = None

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
                    # In half-duplex mode the microphone stream is closed
                    # while the server runs LLM/TTS. Keep receiving server
                    # events instead of waiting forever for a new frame.
                    try:
                        event = await asyncio.wait_for(websocket.recv(), timeout=0.05)
                    except asyncio.TimeoutError:
                        await asyncio.sleep(0)
                    else:
                        await handle_server_message(
                            websocket,
                            event,
                            args,
                            frame_queue,
                            capture_paused,
                            server_events,
                            stop_capture,
                            start_capture,
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
                        capture_paused,
                        server_events,
                        stop_capture,
                        start_capture,
                    )
        except KeyboardInterrupt:
            print()
            print("Stopping stream by user request...")
        finally:
            capture_resumable = False
            stop_capture()

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
                    capture_paused,
                    server_events,
                    stop_capture,
                    start_capture,
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
                capture_paused,
                server_events,
                stop_capture,
                start_capture,
            )

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
        help="InputStream blocksize in frames. Use 0 to let PortAudio choose automatically.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    asyncio.run(run_stream_upload(args))


if __name__ == "__main__":
    main()
