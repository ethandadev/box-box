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
        # If we're behind, dropping beats lagging: drop the oldest datagram. It can be any packet type
        # (an event, a session update), not just a frame the newer one replaces.
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(data)


class PortUnavailable(OSError):
    """The UDP port is taken (another telemetry app?) or reserved by Windows."""

    def __init__(self, port: int, cause: OSError) -> None:
        super().__init__(cause.errno, f"can't listen on UDP port {port}: {cause.strerror or cause}")
        self.port = port
        self.cause = cause


def bind_udp(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> socket.socket:
    """Open the listening socket up front, so a taken port fails before anything else starts."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # No SO_REUSEADDR: on Windows it collides with apps that hold the port exclusively (WinError 10013),
    # and on Linux it would let two listeners silently split the packets.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    try:
        sock.bind((host, port))
    except OSError as e:
        sock.close()
        raise PortUnavailable(port, e) from e
    return sock


async def udp_source(sock: socket.socket) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
    transport, _ = await loop.create_datagram_endpoint(lambda: _Queueing(queue), sock=sock)
    try:
        while True:
            yield await queue.get()
    finally:
        transport.close()


async def file_source(path: str | Path, speed: float = 1.0) -> AsyncIterator[bytes]:
    """Replay a recording. speed=0 means as fast as possible."""
    start = time.monotonic()
    for n, (t_ns, data) in enumerate(read_records(path)):
        delay = start + t_ns / 1e9 / speed - time.monotonic() if speed > 0 else 0.0
        if delay > 0:
            await asyncio.sleep(delay)
        elif n % 200 == 0:
            await asyncio.sleep(0)  # running behind (or flat out): still let the UI and the voice breathe
        yield data
