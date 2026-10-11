"""`.f1raw` recordings: raw UDP datagrams with timestamps, for replaying sessions without the game.

Layout (compatible with rdgonzaga/f1-game-telemetry): 8-byte header (`F1RAW\\0` + u16 version), then records
of `<u64 ns since recording start><u16 length><datagram>`. Little-endian.
"""

from __future__ import annotations

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
    def __init__(self, path: str | Path) -> None:
        self._file: BinaryIO = open(path, "wb")  # noqa: SIM115
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
    with open(path, "rb") as f:
        head = f.read(FILE_HEADER.size)
        if len(head) < FILE_HEADER.size:
            raise RawFileError(f"{path}: not an .f1raw file (too short)")
        magic, version = FILE_HEADER.unpack(head)
        if magic != MAGIC:
            raise RawFileError(f"{path}: not an .f1raw file (bad magic)")
        if version != VERSION:
            raise RawFileError(f"{path}: unsupported .f1raw version {version}")
        while True:
            rec = f.read(RECORD_HEADER.size)
            if len(rec) < RECORD_HEADER.size:
                return
            t_ns, length = RECORD_HEADER.unpack(rec)
            data = f.read(length)
            if len(data) < length:
                return
            yield t_ns, data
