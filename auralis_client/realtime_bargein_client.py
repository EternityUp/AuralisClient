from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Event

import sounddevice as sd

from auralis_client.audio_io import DuplexAudioEngine
from auralis_client.config import CLIENT_SAMPLE_RATE


def is_wasapi_duplex_pair(input_device: int, output_device: int) -> bool:
    input_info = sd.query_devices(input_device, "input")
    output_info = sd.query_devices(output_device, "output")
    input_hostapi = sd.query_hostapis(int(input_info["hostapi"]))
    output_hostapi = sd.query_hostapis(int(output_info["hostapi"]))
    return input_hostapi["name"] == "Windows WASAPI" and output_hostapi["name"] == "Windows WASAPI"


async def run_client(args: argparse.Namespace) -> None:
    try:
        websockets = __import__("websockets")
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing dependency: websockets\nInstall it with:\n  python -m pip install websockets") from exc

    if not is_wasapi_duplex_pair(args.input_device, args.output_device):
        raise SystemExit(
            "The realtime barge-in client currently requires both selected endpoints to use Windows WASAPI. "
            "Use --list-devices to find the WASAPI HP21 microphone and speaker entries."
        )

    frame_queue: Queue[bytes] = Queue()
    running = asyncio.Event()
    running.set()
    stopped = asyncio.Event()
    reply_tasks: set[asyncio.Task[None]] = set()
    interrupted_tokens: set[int] = set()
    sent_frames = 0
    sent_bytes = 0
    server_events = 0
    receiver_error: Exception | None = None
    shutting_down = False
    started = time.perf_counter()

    def on_frame(frame: bytes) -> None:
        frame_queue.put(frame)

    engine = DuplexAudioEngine(
        input_device=args.input_device,
        output_device=args.output_device,
        input_frame_callback=on_frame,
        frame_duration_ms=args.frame_ms,
        blocksize_frames=args.blocksize_frames,
        latency=args.duplex_latency,
    )

    metadata = {
        "type": "stream_start",
        "client": "AuralisClientRealtimeBargeIn",
        "format": "pcm_s16le",
        "sample_rate": CLIENT_SAMPLE_RATE,
        "channels": 1,
        "frame_ms": args.frame_ms,
        "client_time": time.time(),
    }

    async with websockets.connect(args.server_url, open_timeout=args.timeout, max_size=None) as websocket:
        send_lock = asyncio.Lock()

        async def send_json(payload: dict[str, object]) -> None:
            async with send_lock:
                await websocket.send(json.dumps(payload, ensure_ascii=False))

        async def send_frames() -> None:
            nonlocal sent_frames, sent_bytes
            while running.is_set():
                try:
                    frame = await asyncio.to_thread(frame_queue.get, True, 0.2)
                except Empty:
                    continue
                async with send_lock:
                    await websocket.send(frame)
                sent_frames += 1
                sent_bytes += len(frame)

        async def play_reply(reply_path: Path, response_token: int, reply_id: str) -> None:
            nonlocal shutting_down
            completion: Event = engine.enqueue_wav(reply_path)
            await send_json(
                {
                    "type": "playback_started",
                    "response_token": response_token,
                    "reply_id": reply_id,
                    "client_time": time.time(),
                }
            )
            await asyncio.to_thread(completion.wait)
            interrupted = response_token in interrupted_tokens
            interrupted_tokens.discard(response_token)
            if not shutting_down:
                await send_json(
                    {
                        "type": "playback_completed",
                        "response_token": response_token,
                        "reply_id": reply_id,
                        "interrupted": interrupted,
                        "client_time": time.time(),
                    }
                )
            print(f"REPLY_PLAYBACK_COMPLETED: response_token={response_token}, interrupted={interrupted}")

        async def receive_events() -> None:
            nonlocal receiver_error, server_events
            try:
                while True:
                    message = await websocket.recv()
                    if isinstance(message, bytes):
                        print(f"Unexpected binary server message: {len(message)} bytes")
                        continue
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        print("SERVER_TEXT:")
                        print(message)
                        continue
                    server_events += 1
                    message_type = payload.get("type")

                    if message_type == "reply_audio":
                        audio_bytes = await asyncio.wait_for(websocket.recv(), timeout=args.timeout)
                        if not isinstance(audio_bytes, bytes):
                            raise RuntimeError("Expected reply WAV bytes after reply_audio metadata.")
                        filename = Path(str(payload.get("filename", "reply.wav"))).name
                        reply_path = Path(args.reply_output_dir) / filename
                        reply_path.parent.mkdir(parents=True, exist_ok=True)
                        reply_path.write_bytes(audio_bytes)
                        response_token = int(payload.get("response_token", -1))
                        reply_id = str(payload.get("reply_id", filename))
                        print("REPLY_AUDIO_META:")
                        print(message)
                        print(f"REPLY_AUDIO_OUTPUT: {reply_path}")
                        task = asyncio.create_task(play_reply(reply_path, response_token, reply_id))
                        reply_tasks.add(task)
                        task.add_done_callback(reply_tasks.discard)
                        continue

                    print("SERVER_EVENT:")
                    print(message)
                    if message_type == "barge_in":
                        raw_response_token = payload.get("interrupted_response_token")
                        response_token = int(raw_response_token) if raw_response_token is not None else -1
                        if response_token >= 0:
                            interrupted_tokens.add(response_token)
                        cleared = engine.clear_playback()
                        print(f"BARGE_IN: cleared {cleared} local playback buffer(s).")
                        await send_json(
                            {
                                "type": "barge_in_ack",
                                "interrupted_response_token": response_token,
                                "cleared_playbacks": cleared,
                                "client_time": time.time(),
                            }
                        )
                    elif message_type == "stream_realtime_stopped":
                        stopped.set()
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                receiver_error = exc
                print(f"Realtime receive loop failed: {exc}")
                stopped.set()

        await send_json(metadata)
        ready = await asyncio.wait_for(websocket.recv(), timeout=args.timeout)
        print("SERVER_READY:")
        print(ready)

        sender_task = asyncio.create_task(send_frames())
        receiver_task = asyncio.create_task(receive_events())
        drain_reply_playback = True
        try:
            engine.start()
            print(
                f"Continuous duplex streaming for {args.seconds:.1f} seconds. "
                "Press Ctrl+C to stop early."
            )
            await asyncio.sleep(args.seconds)
        except KeyboardInterrupt:
            print()
            print("Stopping realtime stream by user request...")
            drain_reply_playback = False
        finally:
            running.clear()
            shutting_down = True
            await send_json(
                {
                    "type": "stream_stop",
                    "client_time": time.time(),
                    "frames": sent_frames,
                    "bytes": sent_bytes,
                }
            )
            try:
                await asyncio.wait_for(stopped.wait(), timeout=args.timeout)
                if drain_reply_playback and reply_tasks:
                    pending_replies = tuple(reply_tasks)
                    print(f"Waiting for {len(pending_replies)} queued reply playback(s) to finish...")
                    await asyncio.gather(*pending_replies, return_exceptions=True)
            finally:
                sender_task.cancel()
                receiver_task.cancel()
                if not drain_reply_playback:
                    for task in tuple(reply_tasks):
                        task.cancel()
                await asyncio.gather(sender_task, receiver_task, *reply_tasks, return_exceptions=True)
                engine.stop()

        if receiver_error is not None:
            raise RuntimeError(f"Realtime receive loop failed: {receiver_error}") from receiver_error

    elapsed = time.perf_counter() - started
    print(f"CLIENT_FRAMES_SENT: {sent_frames}")
    print(f"CLIENT_BYTES_SENT: {sent_bytes}")
    print(f"CLIENT_STREAM_SECONDS: {elapsed:.3f}")
    print(f"SERVER_EVENTS_RECEIVED: {server_events}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Continuously stream HP21 WASAPI audio and accept LLM-confirmed barge-in events."
    )
    parser.add_argument("--server-url", default="ws://192.168.16.206:8771")
    parser.add_argument("--input-device", type=int, required=True)
    parser.add_argument("--output-device", type=int, required=True)
    parser.add_argument("--reply-output-dir", default="outputs/realtime_bargein_replies")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--frame-ms", type=int, default=100)
    parser.add_argument("--blocksize-frames", type=int, default=0)
    parser.add_argument("--duplex-latency", choices=["low", "high"], default="high")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()
    asyncio.run(run_client(args))


if __name__ == "__main__":
    main()
