"""Logging: a rotating file for bug reports, the console, and a ring buffer the UI shows live."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from collections import deque
from dataclasses import dataclass

from box_box import paths

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


@dataclass(frozen=True, slots=True)
class LogLine:
    time: float
    level: str
    name: str
    message: str


class RingBufferHandler(logging.Handler):
    """Keeps the last N log lines in memory so the Diagnostics page can show them without reading files."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__(logging.INFO)
        self.lines: deque[LogLine] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            with self._lock:
                self.lines.append(LogLine(record.created, record.levelname, record.name, msg))
                self.seq += 1
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)

    def snapshot(self, limit: int = 200) -> list[LogLine]:
        with self._lock:
            return list(self.lines)[-limit:]


ring = RingBufferHandler()
_configured = False


def setup(verbose: bool = False, to_file: bool = True) -> None:
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(logging.DEBUG if verbose else logging.INFO)
        return
    _configured = True
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(console)
    root.addHandler(ring)

    if to_file:
        try:
            handler = logging.handlers.RotatingFileHandler(
                paths.logs_dir() / "box-box.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
            handler.setLevel(logging.DEBUG if verbose else logging.INFO)
            handler.setFormatter(logging.Formatter(FORMAT))
            root.addHandler(handler)
        except OSError as e:  # read-only disk, permissions: keep running, just without a log file
            root.warning("can't write log file: %s", e)

    # Third-party chatter that drowns the useful lines.
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "aiohttp.access", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
