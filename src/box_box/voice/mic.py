"""Microphone capture with a pre-roll buffer, so the first word isn't clipped when the button is pressed."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np

from box_box.voice.player import find_device

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
PRE_ROLL_S = 0.35
MAX_UTTERANCE_S = 30.0
REOPEN_EVERY_S = 3.0


class Microphone:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream = None
        self._ring: deque[np.ndarray] = deque()
        self._ring_len = 0
        self._recording: list[np.ndarray] | None = None
        self._rec_len = 0
        self.level = 0.0  # RMS of the latest block, for the UI meter
        self.error: str | None = None
        self.device_name = ""
        self._lost = False  # stopped on its own: housekeeping keeps trying to get it back
        self._last_reopen = 0.0

    @property
    def ok(self) -> bool:
        return self._stream is not None and self.error is None

    def open(self, device_name: str = "") -> None:
        self.close()
        self.device_name = device_name
        try:
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001
            self.error = f"microphone unavailable: {e}"
            log.error(self.error)
            return
        try:
            index = find_device(device_name, "input")
            self._stream = sd.InputStream(
                device=index,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=800,
                callback=self._callback,
                finished_callback=self._finished,
            )
            self._stream.start()
            self.error = None
            log.info("microphone: %s", sd.query_devices(index, "input")["name"])
        except Exception as e:  # noqa: BLE001
            self._stream = None
            self.error = f"couldn't open the microphone: {e}"
            log.error(self.error)

    def close(self) -> None:
        stream, self._stream = self._stream, None  # cleared first: closing isn't an unplug
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    def reopen_if_lost(self, device_name: str | None = None) -> bool:
        """Housekeeping: if the mic dropped out (unplugged, driver reset), try opening it again.

        Retries at most every REOPEN_EVERY_S until it comes back. Returns True if the mic is working."""
        if not self._lost:
            return self.ok
        now = time.monotonic()
        if now - self._last_reopen < REOPEN_EVERY_S:
            return False
        self._last_reopen = now
        self.open(self.device_name if device_name is None else device_name)
        if self.ok:
            self._lost = False
            log.info("microphone back")
        return self.ok

    def _finished(self) -> None:
        # The stream stopped on its own: the mic was unplugged or the driver reset.
        if self._stream is not None:
            self.error = "microphone stopped (unplugged?)"
            self._lost = True
            log.warning(self.error)

    def _callback(self, indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
        block = indata[:, 0].copy()
        self.feed(block)

    def feed(self, block: np.ndarray) -> None:
        """Add captured audio (also used by tests and the UI's browser mic)."""
        self.level = float(np.sqrt(np.mean(block**2))) if len(block) else 0.0
        with self._lock:
            if self._recording is not None:
                if self._rec_len < MAX_UTTERANCE_S * SAMPLE_RATE:
                    self._recording.append(block)
                    self._rec_len += len(block)
                return
            self._ring.append(block)
            self._ring_len += len(block)
            while self._ring and self._ring_len - len(self._ring[0]) >= PRE_ROLL_S * SAMPLE_RATE:
                self._ring_len -= len(self._ring.popleft())

    def start_recording(self) -> None:
        with self._lock:
            self._recording = list(self._ring)
            self._rec_len = self._ring_len
            self._ring.clear()
            self._ring_len = 0

    def stop_recording(self) -> np.ndarray:
        with self._lock:
            chunks, self._recording = self._recording or [], None
            self._rec_len = 0
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32)

    @property
    def recording(self) -> bool:
        return self._recording is not None
