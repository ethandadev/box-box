"""Text-to-speech engines: Kokoro (natural neural voice, local), Windows SAPI (fallback), and silent.

All synthesis happens on one worker thread (neither Kokoro's ONNX session nor SAPI likes being called
from several threads), and results are cached so repeated phrases are instant.
"""

from __future__ import annotations

import itertools
import logging
import queue
import sys
import threading
import time
import wave
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path

import numpy as np

from box_box.voice.models import ModelDownloader, kokoro_files
from box_box.voice.pronounce import to_phonemes

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
    MODELS = ("kokoro-v1.0.onnx", "kokoro-v1.0.int8.onnx")  # best first; int8 is the old, rougher download

    def __init__(self, files: ModelDownloader = kokoro_files) -> None:
        self.files = files
        self._kokoro = None
        self.model: str | None = None

    def available_model(self) -> str | None:
        voices = self.files.path("voices-v1.0.bin")
        if not voices.exists():
            return None
        for name in self.MODELS:
            p = self.files.path(name)
            if p.exists() and p.stat().st_size > 50_000_000:
                return name
        return None

    @property
    def upgradable(self) -> bool:
        """Running on the rougher int8 model while the full one is now on disk."""
        return self.model is not None and self.model != self.MODELS[0] and self.available_model() == self.MODELS[0]

    def reload(self) -> None:
        self._kokoro, self.model = None, None

    def load(self) -> None:
        if self._kokoro is not None:
            return
        model = self.available_model()
        if model is None:
            raise TTSUnavailable("Kokoro voice files aren't downloaded yet")
        try:
            from kokoro_onnx import Kokoro
        except Exception as e:  # noqa: BLE001
            raise TTSUnavailable(f"kokoro-onnx isn't installed: {e}") from e
        kokoro = Kokoro(str(self.files.path(model)), str(self.files.path("voices-v1.0.bin")))
        kokoro.create("Radio check.", voice="am_michael", speed=1.0, lang="en-us")  # warm up: the first call is slow
        self._kokoro, self.model = kokoro, model
        log.info("Kokoro voice loaded (%s)", model)

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        self.load()
        kokoro = self._kokoro  # one read: a reload on another thread may clear it at any moment
        if kokoro is None:
            raise TTSUnavailable("Kokoro is reloading")
        lang = "en-gb" if voice.startswith("b") else "en-us"
        # Names in IPA (the dictionary mangles Leclerc, Ocon, Sainz...), everything else phonemized normally.
        phonemes = to_phonemes(text, lambda chunk: kokoro.tokenizer.phonemize(chunk, lang))
        samples, sr = kokoro.create(
            phonemes, voice=voice, speed=speed, lang=lang, is_phonemes=True, sentence_pause=0.18, clause_pause=0.04
        )
        return tighten(np.asarray(samples, dtype=np.float32), int(sr)), int(sr)


def stretch(audio: np.ndarray, sr: int, rate: float) -> np.ndarray:
    """Speed speech up (rate > 1) or slow it down without changing its pitch (WSOLA).

    For voices that can't go as fast as the driver wants on their own: cut into short overlapping
    slices, each nudged to where it lines up best with the last one, and laid down closer together."""
    x = np.asarray(audio, dtype=np.float32)
    if abs(rate - 1.0) < 0.02 or len(x) < sr // 10:
        return x
    frame = int(sr * 0.03)
    hop_out = frame // 2
    hop_in = hop_out * rate
    tol = int(sr * 0.008)
    window = np.hanning(frame).astype(np.float32)
    n_frames = int((len(x) - frame - tol) / hop_in)
    if n_frames < 2:
        return x
    out = np.zeros(n_frames * hop_out + frame, dtype=np.float32)
    norm = np.zeros_like(out)
    prev = 0
    for k in range(n_frames):
        target = int(k * hop_in)
        start = target
        if k:
            # Where the previous slice would naturally have carried on; pick the nearby start that matches it.
            natural = x[prev + hop_out : prev + hop_out + frame]
            lo, hi = max(0, target - tol), min(len(x) - frame, target + tol)
            if hi > lo and len(natural) == frame:
                region = x[lo : hi + frame]
                score = np.correlate(region, natural, mode="valid")
                start = lo + int(np.argmax(score))
        seg = x[start : start + frame]
        if len(seg) < frame:
            break
        at = k * hop_out
        out[at : at + frame] += seg * window
        norm[at : at + frame] += window
        prev = start
    norm[norm < 1e-3] = 1.0
    return (out / norm)[: int(len(x) / rate)].astype(np.float32)


def tighten(audio: np.ndarray, sr: int, max_pause_s: float = 0.24, floor: float = 0.008) -> np.ndarray:
    """Radio delivery is clipped: shorten long pauses the voice model leaves between phrases, and trim the
    ends. Pauses keep a short fade so nothing clicks."""
    if len(audio) < sr // 10:
        return audio
    frame = max(1, int(sr * 0.01))
    n = len(audio) // frame
    if n < 3:
        return audio
    energy = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = energy >= floor
    if not loud.any():
        return audio
    first, last = int(np.argmax(loud)), int(n - 1 - np.argmax(loud[::-1]))
    keep_frames = int(max_pause_s / 0.01)
    pieces: list[np.ndarray] = []
    i = first
    while i <= last:
        if loud[i]:
            j = i
            while j <= last and loud[j]:
                j += 1
            pieces.append(audio[i * frame : j * frame])
            i = j
        else:
            j = i
            while j <= last and not loud[j]:
                j += 1
            run = j - i
            gap = audio[i * frame : (i + min(run, keep_frames)) * frame].copy()
            if run > keep_frames and len(gap) > 4:
                fade = min(len(gap) // 2, frame * 3)
                gap[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
            pieces.append(gap)
            i = j
    # Generous margins: a soft first sound (the V of Verstappen, the L of Leclerc) sits below the loudness
    # floor, and trimming to the first loud frame cut it off.
    lead = audio[max(0, (first - 6) * frame) : first * frame]
    tail = audio[(last + 1) * frame : min(len(audio), (last + 8) * frame)]
    return np.concatenate([lead, *pieces, tail]).astype(np.float32)


class WindowsEngine(Engine):
    """The voice built into Windows (SAPI), spoken into an in-memory stream. Robotic but always there.

    SAPI is a COM object: it's created on the TTS worker thread (where it's used), never on the loop thread.
    """

    name = "windows"
    SAMPLE_RATE = 22_050
    SAFT22kHz16BitMono = 22  # SpeechAudioFormatType

    def __init__(self) -> None:
        self._voice = None

    def load(self) -> None:
        if sys.platform != "win32":
            raise TTSUnavailable("the Windows voice only exists on Windows")
        try:
            import comtypes.client  # noqa: F401
        except Exception as e:  # noqa: BLE001
            raise TTSUnavailable(f"Windows speech isn't available: {e}") from e

    def _get(self):  # type: ignore[no-untyped-def]
        if self._voice is None:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            if voice.GetVoices().Count == 0:
                raise TTSUnavailable("no Windows voices are installed (Settings > Time & language > Speech)")
            self._voice = voice
        return self._voice

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        self.load()
        import comtypes.client

        sapi = self._get()
        stream = comtypes.client.CreateObject("SAPI.SpMemoryStream")
        fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
        fmt.Type = self.SAFT22kHz16BitMono
        stream.Format = fmt
        sapi.AudioOutputStream = stream
        sapi.Rate = max(-10, min(10, round((speed - 1.0) * 10)))
        sapi.Speak(text, 0)  # synchronous
        raw = bytes(stream.GetData())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) == 0:
            raise TTSUnavailable("the Windows voice produced no audio")
        return samples, self.SAMPLE_RATE


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
DEAD_RETRY_S = 600.0  # a cloud voice that rejected us (key, credits) is retried this rarely
FLAKY_RETRY_S = 60.0  # after repeated network failures, give the cloud voice a minute off


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
        self._dead: dict[str, float] = {}  # cloud engine -> when it rejected us; skipped until DEAD_RETRY_S
        self._dead_for: dict[str, float] = {}  # how long that skip lasts
        self._failures: dict[str, int] = {}  # consecutive network failures per cloud engine
        self._used_fallback = False  # the last synthesis on the worker came from a stand-in voice

    def choose(self, preferred: str) -> str:
        """Load the preferred engine, falling back to the next one that works. Returns the engine used."""
        order = [preferred] + [e for e in ("kokoro", "windows", "off") if e != preferred]
        self._dead.clear()  # settings changed (a new key, perhaps): give every voice a fresh chance
        self._failures.clear()
        errors: list[str] = []
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

    @property
    def degraded(self) -> bool:
        """A cloud voice refused us and lines are being said by the local voice instead."""
        return bool(self._dead)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

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
            if existing is not None and existing.done():  # cancelled or failed: never hand that out again
                self._inflight.pop(key, None)
                existing = None
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
                if fut is not None and fut.done():
                    self._inflight.pop(key, None)  # nobody's waiting any more (timed out, cancelled)
            if fut is None or fut.done():
                continue  # already synthesized via a promoted copy, or abandoned
            if priority == PREFETCH and time.monotonic() - queued_at > PREFETCH_MAX_AGE_S:
                with self._lock:
                    self._inflight.pop(key, None)
                fut.cancel()
                continue
            self._used_fallback = False
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
                if not self._used_fallback:  # a stand-in voice's audio must not stick to the real voice's key
                    self._cache[key] = audio
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            if not fut.done():
                fut.set_result(audio)

    def _synth(self, key: tuple, text: str, voice: str, speed: float) -> Audio:
        engine = self.engines.get(key[0]) or self.engines["off"]
        cloud = engine.name not in ("kokoro", "windows", "off")
        dead_since = self._dead.get(engine.name)
        if cloud and dead_since is not None and time.monotonic() - dead_since < self._dead_for.get(engine.name, 0):
            audio = self._fallback(text, voice, speed, None)  # don't wait on a voice we know will refuse
            self._used_fallback = False  # benched for a while: caching the stand-in saves synthesizing twice
            return audio
        try:
            audio = engine.synthesize(text, voice, speed)
            self._failures.pop(engine.name, None)
            if dead_since is not None:
                self._dead.pop(engine.name, None)
                self.error, self.status = None, "ready"
                self.clear_cache()  # lines cached in the stand-in voice: say them in the real one again
            return audio
        except TTSUnavailable as e:
            # Rejected key or no credits left: it won't fix itself mid-session. Say every line with the
            # local voice from now on (one consistent voice, no wait for a refusal first), and say why.
            if not cloud:
                if engine.name == "kokoro":
                    return self._fallback(text, voice, speed, e, skip_kokoro=True)
                raise
            log.warning("%s voice unavailable, using the local voice: %s", engine.name, e)
            self._dead[engine.name], self._dead_for[engine.name] = time.monotonic(), DEAD_RETRY_S
            self.error = str(e)
            self.status = f"{e} Using the local voice for now."
            return self._fallback(text, voice, speed, e)
        except Exception as e:
            log.warning("speech synthesis failed with %s: %s", engine.name, e)
            self.error = f"{engine.name}: {e}"
            if not cloud and engine.name != "kokoro":
                raise
            if cloud:
                n = self._failures[engine.name] = self._failures.get(engine.name, 0) + 1
                if n >= 2:  # the network's struggling: don't make every line wait for a timeout
                    self._dead[engine.name], self._dead_for[engine.name] = time.monotonic(), FLAKY_RETRY_S
            return self._fallback(text, voice, speed, e, skip_kokoro=engine.name == "kokoro")

    def _fallback(self, text: str, voice: str, speed: float, err: Exception | None, skip_kokoro: bool = False) -> Audio:
        self._used_fallback = True
        if not skip_kokoro and "kokoro" in self.engines:
            try:  # a cloud voice failed (network, quota): say it with the local voice instead
                return self.engines["kokoro"].synthesize(text, voice, speed)
            except Exception:  # noqa: BLE001
                log.warning("Kokoro fallback failed too")
        if "windows" in self.engines:
            try:
                return self.engines["windows"].synthesize(text, voice, speed)
            except Exception:  # noqa: BLE001
                pass
        if err is not None:
            raise err
        raise TTSUnavailable("no voice available")

    def shutdown(self) -> None:
        self._queue.put((-1, next(self._seq), 0.0, None, "", "", 1.0))
