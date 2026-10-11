"""Text-to-speech engines: Kokoro (natural neural voice, local), Windows SAPI (fallback), and silent.

All synthesis happens on one worker thread (neither Kokoro's ONNX session nor SAPI likes being called
from several threads), and results are cached so repeated phrases are instant.
"""

from __future__ import annotations

import itertools
import logging
import os
import queue
import sys
import tempfile
import threading
import time
import wave
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path

import numpy as np

from box_box.voice.models import ModelDownloader, kokoro_files

log = logging.getLogger(__name__)

Audio = tuple[np.ndarray, int]


class TTSUnavailable(RuntimeError):
    pass


class Engine:
    name = "base"

    def load(self) -> None:  # noqa: B027 - optional
        pass

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        raise NotImplementedError


class KokoroEngine(Engine):
    name = "kokoro"

    def __init__(self, files: ModelDownloader = kokoro_files) -> None:
        self.files = files
        self._kokoro = None

    def load(self) -> None:
        if self._kokoro is not None:
            return
        if not self.files.installed():
            raise TTSUnavailable("Kokoro voice files aren't downloaded yet")
        try:
            from kokoro_onnx import Kokoro
        except Exception as e:  # noqa: BLE001
            raise TTSUnavailable(f"kokoro-onnx isn't installed: {e}") from e
        self._kokoro = Kokoro(str(self.files.path("kokoro-v1.0.int8.onnx")), str(self.files.path("voices-v1.0.bin")))

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        self.load()
        assert self._kokoro is not None
        lang = "en-gb" if voice.startswith("b") else "en-us"
        samples, sr = self._kokoro.create(text, voice=voice, speed=speed, lang=lang)
        return np.asarray(samples, dtype=np.float32), int(sr)


class WindowsEngine(Engine):
    """The voice built into Windows, via SAPI. Robotic but always there."""

    name = "windows"

    def __init__(self) -> None:
        self._engine = None

    def load(self) -> None:
        if self._engine is not None:
            return
        if sys.platform != "win32":
            raise TTSUnavailable("the Windows voice only exists on Windows")
        try:
            import pyttsx3

            self._engine = pyttsx3.init()
        except Exception as e:  # noqa: BLE001
            raise TTSUnavailable(f"Windows speech isn't available: {e}") from e

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        self.load()
        assert self._engine is not None
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            self._engine.setProperty("rate", int(185 * speed))
            self._engine.save_to_file(text, name)
            self._engine.runAndWait()
            return read_wav(Path(name))
        finally:
            Path(name).unlink(missing_ok=True)


class SilentEngine(Engine):
    name = "off"

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        return np.zeros(0, dtype=np.float32), 24_000


def read_wav(path: Path) -> Audio:
    with wave.open(str(path)) as w:
        sr, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        data = (data - 128) / 128
    else:
        data /= float(np.iinfo(dtype).max)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr


URGENT, PREFETCH = 0, 1
PREFETCH_MAX_AGE_S = 20.0  # a prefetch still queued after this long is for a call that's gone stale


class TTS:
    """Picks an engine from settings with automatic fallback, caches results, runs off the event loop.

    One worker thread drains a priority queue: audio that's about to be played jumps ahead of prefetching,
    identical requests share one synthesis, and old prefetches are dropped instead of piling up.
    """

    def __init__(self, engines: dict[str, Engine] | None = None, cache_size: int = 300) -> None:
        self.engines = engines or {"kokoro": KokoroEngine(), "windows": WindowsEngine(), "off": SilentEngine()}
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._inflight: dict[tuple, Future[Audio]] = {}
        self._cache: OrderedDict[tuple, Audio] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="tts", daemon=True)
        self._worker.start()
        self.active: str = "off"
        self.status: str = "not started"
        self.error: str | None = None

    def choose(self, preferred: str) -> str:
        """Load the preferred engine, falling back to the next one that works. Returns the engine used."""
        order = [preferred] + [e for e in ("kokoro", "windows", "off") if e != preferred]
        errors = []
        for name in order:
            engine = self.engines.get(name)
            if engine is None:
                continue
            try:
                engine.load()
                self.active = name
                self.error = "; ".join(errors) or None
                self.status = "ready" if name == preferred else f"using {name} ({errors[0] if errors else ''})"
                if name != preferred:
                    log.warning("voice engine %s unavailable, using %s: %s", preferred, name, "; ".join(errors))
                return name
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
        self.active, self.status = "off", "no voice engine available"
        return "off"

    def submit(self, text: str, voice: str, speed: float, urgent: bool = True) -> Future[Audio]:
        key = (self.active, voice, round(speed, 2), text)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                fut: Future[Audio] = Future()
                fut.set_result(cached)
                return fut
            existing = self._inflight.get(key)
            if existing is not None:
                if urgent:  # promote: queue it again at the front; the worker skips whichever runs second
                    self._queue.put((URGENT, next(self._seq), time.monotonic(), key, text, voice, speed))
                return existing
            fut = Future()
            self._inflight[key] = fut
        self._queue.put((URGENT if urgent else PREFETCH, next(self._seq), time.monotonic(), key, text, voice, speed))
        return fut

    def _run(self) -> None:
        while True:
            priority, _, queued_at, key, text, voice, speed = self._queue.get()
            if key is None:
                return
            with self._lock:
                fut = self._inflight.get(key)
            if fut is None or fut.done():
                continue  # already synthesized via a promoted copy
            if priority == PREFETCH and time.monotonic() - queued_at > PREFETCH_MAX_AGE_S:
                with self._lock:
                    self._inflight.pop(key, None)
                fut.cancel()
                continue
            try:
                audio = self._synth(key, text, voice, speed)
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    self._inflight.pop(key, None)
                if not fut.done():
                    fut.set_exception(e)
                continue
            with self._lock:
                self._inflight.pop(key, None)
                self._cache[key] = audio
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            if not fut.done():
                fut.set_result(audio)

    def _synth(self, key: tuple, text: str, voice: str, speed: float) -> Audio:
        engine = self.engines.get(key[0]) or self.engines["off"]
        try:
            return engine.synthesize(text, voice, speed)
        except Exception as e:
            log.exception("speech synthesis failed with %s", engine.name)
            self.error = f"{engine.name}: {e}"
            if engine.name != "windows" and "windows" in self.engines:
                try:
                    return self.engines["windows"].synthesize(text, voice, speed)
                except Exception:  # noqa: BLE001
                    raise e from None
            raise

    def shutdown(self) -> None:
        self._queue.put((-1, next(self._seq), 0.0, None, "", "", 1.0))
