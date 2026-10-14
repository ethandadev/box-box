"""Record every live session to `<recordings>/<date> <track> <session>.f1raw.gz`, keeping the newest N.

A recording starts with the first packet of a new session and ends on the session-end event, a new
session, or 2 minutes of silence (quit to menu, crash). Files are named once the track is known.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from box_box.telemetry import names
from box_box.telemetry.rawfile import RawWriter

log = logging.getLogger(__name__)

IDLE_CLOSE_S = 120.0


def _safe(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text).strip() or "session"


class AutoRecorder:
    def __init__(self, folder: Path, keep: int = 30) -> None:
        self.folder = folder
        self.keep = keep
        self._writer: RawWriter | None = None
        self._path: Path | None = None
        self._uid: int | None = None
        self._closed_uid: int | None = None  # a session that ended (chequered flag/results): don't reopen it
        self._label: str | None = None
        self._weekend_seen = False  # the label was made with the weekend structure known
        self._started_ns = 0
        self._last_packet = 0.0
        self._last_flush = 0.0
        self.last_file: Path | None = None
        self.error: str | None = None

    @property
    def active(self) -> bool:
        return self._writer is not None

    @property
    def current(self) -> Path | None:
        return self._path

    def feed(
        self,
        data: bytes,
        session_uid: int,
        track_id: int | None,
        session_type: int | None,
        weekend: tuple[int, ...] = (),
    ) -> None:
        now = time.monotonic()
        if session_uid == 0:
            return
        if self._writer is not None and session_uid != self._uid:
            self.close()
        if self._writer is None and session_uid == self._closed_uid:
            return  # the results screen after the flag: same session, already saved
        if self._writer is None:
            self._open(session_uid)
        if self._writer is None:
            return
        # The first Session packet of a session can arrive before the game fills in the weekend
        # structure, and without it a sprint reads as "Race". Keep relabelling until it shows up.
        if (self._label is None or not self._weekend_seen) and track_id is not None and session_type is not None:
            self._label = f"{names.track_name(track_id)} {names.session_name(session_type, weekend)}"
            self._weekend_seen = bool(weekend)
        try:
            # Timestamps are taken when we process the packet, not when it arrived, so a backlog in
            # the queue is squashed together on replay.
            self._writer.write(time.monotonic_ns() - self._started_ns, data)
            if now - self._last_flush > 2.0:
                self._writer.flush()
                self._last_flush = now
        except OSError as e:  # disk full, say: stop for this session, don't churn new files
            self.error = f"recording stopped: {e}"
            log.error(self.error)
            writer, self._writer = self._writer, None
            with contextlib.suppress(Exception):
                writer.close()
            self._closed_uid, self._path, self._uid = self._uid, None, None
        self._last_packet = now

    def tick(self) -> None:
        """Close an abandoned recording (no packets for a while)."""
        if self._writer is not None and time.monotonic() - self._last_packet > IDLE_CLOSE_S:
            self.close()

    def _open(self, uid: int) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._path = self.folder / f"{datetime.now():%Y-%m-%d %H%M%S} recording.f1raw.gz"
            self._writer = RawWriter(self._path)
            self._uid, self._label, self._weekend_seen = uid, None, False
            self._started_ns = time.monotonic_ns()
            self.error = None
            log.info("recording session to %s", self._path)
        except OSError as e:
            self.error = f"can't record: {e}"
            log.error(self.error)
            self._writer = None

    def close(self, ended: bool = False) -> Path | None:
        """Finish the current file. `ended`: the session itself is over, so later packets from it (the
        results screen) don't start another recording."""
        writer, path = self._writer, self._path
        if ended:
            self._closed_uid = self._uid
        self._writer, self._path, self._uid = None, None, None
        if writer is None or path is None:
            return None
        try:
            writer.close()
            if writer.count < 200:  # a menu blip, not a session
                path.unlink(missing_ok=True)
                return None
            final = path
            if self._label:
                final = path.with_name(path.name.replace("recording", _safe(self._label)))
                n = 2
                while final.exists():  # never overwrite another recording
                    final = path.with_name(path.name.replace("recording", f"{_safe(self._label)} ({n})"))
                    n += 1
                path.replace(final)
            self.last_file = final
            log.info("saved recording %s (%d packets)", final.name, writer.count)
            self._prune()
            return final
        except OSError as e:
            log.error("couldn't finish recording %s: %s", path, e)
            return None

    def _prune(self) -> None:
        # Only our own recordings ("2026-10-13 171817 ..."): files the driver put here are theirs.
        ours = [p for p in self.folder.glob("*.f1raw*") if re.match(r"\d{4}-\d{2}-\d{2} \d{6} ", p.name)]
        files = sorted(ours, key=_mtime, reverse=True)
        for old in files[self.keep :]:
            try:
                old.unlink()
            except OSError:
                pass


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:  # deleted between listing and looking
        return 0.0


def list_recordings(folder: Path) -> list[dict]:
    out = []
    for p in sorted(folder.glob("*.f1raw*"), key=_mtime, reverse=True):
        try:
            st = p.stat()
        except OSError:  # pruned while we were listing
            continue
        out.append({"name": p.name, "size_mb": round(st.st_size / 1e6, 1), "modified": st.st_mtime})
    return out
