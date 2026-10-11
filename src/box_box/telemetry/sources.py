"""Where datagrams come from: the live game over UDP, or a recording replayed in (scaled) real time."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import AsyncIterator
from pathlib import Path

from box_box.telemetry.rawfile import read_records

DEFAULT_PORT = 20777


class _Queueing(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self.queue = queue

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # If we're behind, dropping beats lagging: the next frame supersedes this one.
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(data)


async def udp_source(port: int = DEFAULT_PORT, host: str = "0.0.0.0") -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind((host, port))
    transport, _ = await loop.create_datagram_endpoint(lambda: _Queueing(queue), sock=sock)
    try:
        while True:
            yield await queue.get()
    finally:
        transport.close()


async def file_source(path: str | Path, speed: float = 1.0) -> AsyncIterator[bytes]:
    """Replay a recording. speed=0 means as fast as possible."""
    start = time.monotonic()
    for t_ns, data in read_records(path):
        if speed > 0:
            due = start + t_ns / 1e9 / speed
            delay = due - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        yield data
