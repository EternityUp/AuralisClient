from __future__ import annotations

import argparse
import asyncio
import json
import time


async def ping_once(server_url: str, timeout: float) -> None:
    try:
        websockets = __import__("websockets")
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing dependency: websockets\nInstall it with:\n  python -m pip install websockets") from exc

    started = time.perf_counter()
    async with websockets.connect(server_url, open_timeout=timeout) as websocket:
        payload = {
            "type": "ping",
            "client_time": time.time(),
            "client": "AuralisClient",
        }
        await websocket.send(json.dumps(payload, ensure_ascii=False))
        raw_response = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    elapsed_ms = (time.perf_counter() - started) * 1000

    print(f"SERVER_URL: {server_url}")
    print(f"ROUND_TRIP_MS: {elapsed_ms:.1f}")
    print("RESPONSE:")
    print(raw_response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test WebSocket connectivity to the Auralis server.")
    parser.add_argument("--server-url", default="ws://192.168.16.206:8765")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    asyncio.run(ping_once(args.server_url, args.timeout))


if __name__ == "__main__":
    main()
