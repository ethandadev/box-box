"""`.f1raw` recordings: raw UDP datagrams with timestamps, for replaying sessions without the game.

Layout (compatible with rdgonzaga/f1-game-telemetry): 8-byte header (`F1RAW\\0` + u16 version), then records
of `<u64 ns since recording start><u16 length><datagram>`. Little-endian.

A path ending in `.gz` is gzip-compressed (about 7x smaller); reading detects gzip by its magic bytes.
"""

from __future__ import annotations

import gzip
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Self

MAGIC = b"F1RAW\x00"
VERSION = 1
FILE_HEADER = struct.Struct("<6sH")
RECORD_HEADER = struct.Struct("<QH")


class RawFileError(ValueError):
    pass


class RawWriter:
    def __init__(self, path: str | Path, level: int = 6) -> None:
        self._file: BinaryIO = (
            gzip.open(path, "wb", compresslevel=level) if str(path).endswith(".gz") else open(path, "wb")  # noqa: SIM115
        )
        self._file.write(FILE_HEADER.pack(MAGIC, VERSION))
        self.count = 0

    def write(self, t_ns: int, data: bytes) -> None:
        # One write per record so an interrupt never leaves a half-written record behind.
        self._file.write(RECORD_HEADER.pack(t_ns, len(data)) + data)
        self.count += 1

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def read_records(path: str | Path) -> Iterator[tuple[int, bytes]]:
    """Yield `(t_ns, datagram)`; a truncated trailing record (e.g. from a crash mid-write) is ignored."""
    with open(path, "rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    with gzip.open(path, "rb") if compressed else open(path, "rb") as f:  # type: ignore[attr-defined]
        head = f.read(FILE_HEADER.size)
        if len(head) < FILE_HEADER.size:
            raise RawFileError(f"{path}: not an .f1raw file (too short)")
        magic, version = FILE_HEADER.unpack(head)
        if magic != MAGIC:
            raise RawFileError(f"{path}: not an .f1raw file (bad magic)")
        if version != VERSION:
            raise RawFileError(f"{path}: unsupported .f1raw version {version}")
        while True:
            try:
                rec = f.read(RECORD_HEADER.size)
                if len(rec) < RECORD_HEADER.size:
                    return
                t_ns, length = RECORD_HEADER.unpack(rec)
                data = f.read(length)
            except EOFError:
                return  # gzip stream cut off (recording killed): keep what we have
            if len(data) < length:
                return
            yield t_ns, data


# For sharing a session: the packets the engineer uses, at the rate he needs them. About 4x smaller.
_SHARE_RATE_HZ = {
    6: 10.0,
    7: 10.0,
    16: 10.0,
    10: 2.0,
    11: 4.0,
    12: 0.5,  # tyre sets, per car
}  # telemetry, status, t2, damage, history (lap data: all)
_SHARE_KEEP = {1, 2, 3, 4, 6, 7, 8, 10, 11, 12, 16}  # no motion, setups, lobby, motion ex, time trial, positions


def compact(src: str | Path, dest: str | Path) -> int:
    """Write a smaller copy of a recording that still replays the same through the engineer.
    Returns the number of datagrams kept."""
    last: dict[tuple[int, int], int] = {}
    kept = 0
    with RawWriter(dest, level=9) as out:
        for t_ns, data in read_records(src):
            if len(data) < 7:
                continue
            pid = data[6]
            if pid not in _SHARE_KEEP:
                continue
            # Session history comes one car per packet, in rotation: thin it per car, not overall.
            stream = (pid, data[29] if pid in (11, 12) and len(data) > 29 else -1)
            rate = _SHARE_RATE_HZ.get(pid)
            if rate and t_ns - last.get(stream, -(10**18)) < 1e9 / rate:
                continue
            last[stream] = t_ns
            out.write(t_ns, data)
            kept += 1
    return kept
