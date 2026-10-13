"""Audio output: one always-open stream so the radio starts instantly, with cut-off for barge-in.

`sounddevice` (PortAudio) is imported lazily: if it's missing or there's no output device, a
`NullPlayer` stands in, so the rest of the app keeps working and the UI can say what's wrong.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass

import numpy as np

from box_box.voice.fx import resample

log = logging.getLogger(__name__)

# Bluetooth headsets (and some Windows drivers) go to sleep on digital silence and take a few hundred ms
# to wake when sound starts again, eating the start of every message. A noise floor far below hearing
# (-80 dBFS) keeps them awake; a short pad in front of the first clip after a long quiet covers the rest.
KEEP_ALIVE_LEVEL = 1e-4
WAKE_AFTER_IDLE_S = 4.0
WAKE_PAD_S = 0.15


@dataclass
class Device:
    index: int
    name: str
    default: bool
    hostapi: str


def list_devices(kind: str = "output") -> list[Device]:
    """Audio devices for the settings dropdown. Empty if PortAudio isn't available."""
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return []
    try:
        devices = sd.query_devices()
        apis = sd.query_hostapis()
        default_in, default_out = sd.default.device
    except Exception:  # noqa: BLE001
        return []
    out = []
    seen = set()
    for i, d in enumerate(devices):
        channels = d["max_output_channels"] if kind == "output" else d["max_input_channels"]
        if channels <= 0:
            continue
        api = apis[d["hostapi"]]["name"]
        # Windows lists every device once per host API; prefer the shared-mode WASAPI/MME names once.
        key = d["name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(Device(i, d["name"], i == (default_out if kind == "output" else default_in), api))
    return out


def find_device(name: str, kind: str) -> int | None:
    if not name:
        return None
    for d in list_devices(kind):
        if d.name == name:
            return d.index
    lowered = name.lower()
    for d in list_devices(kind):
        if lowered in d.name.lower():
            return d.index
    return None


class _Clip:
    __slots__ = ("data", "pos", "future")

    def __init__(self, data: np.ndarray, future: Future[bool]) -> None:
        self.data = data
        self.pos = 0
        self.future = future


class AudioPlayer:
    """Plays mono float32 clips. `play` returns a Future that resolves True when finished, False if cut off."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clips: deque[_Clip] = deque()
        self._overlay: deque[_Clip] = deque()  # short sounds mixed on top right away (the driver's key clicks)
        self._stream = None
        self.samplerate = 48_000
        self.volume = 0.9
        self.device_name = ""
        self.error: str | None = None
        self.level = 0.0  # output level for the UI meter
        self.keep_alive = True
        self._last_sound = 0.0  # monotonic time the last clip finished playing
        self._noise = np.random.default_rng(0)

    @property
    def ok(self) -> bool:
        return self._stream is not None and self.error is None

    def open(self, device_name: str = "") -> None:
        self.close()
        self.device_name = device_name
        try:
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001
            self.error = f"audio output unavailable: {e}"
            log.error(self.error)
            return
        try:
            index = find_device(device_name, "output")
            if device_name and index is None:
                log.warning("output device %r not found; using the default", device_name)
            info = sd.query_devices(index, "output")
            self.samplerate = int(info["default_samplerate"]) or 48_000
            channels = min(2, int(info["max_output_channels"]) or 1)
            self._channels = channels
            self._stream = sd.OutputStream(
                device=index,
                samplerate=self.samplerate,
                channels=channels,
                dtype="float32",
                latency="low",
                callback=self._callback,
                finished_callback=self._finished,
            )
            self._stream.start()
            self.error = None
            log.info("audio output: %s @ %d Hz", info["name"], self.samplerate)
        except Exception as e:  # noqa: BLE001
            self._stream = None
            self.error = f"couldn't open audio output: {e}"
            log.error(self.error)

    def close(self) -> None:
        self.stop()
        stream, self._stream = self._stream, None  # cleared first: closing isn't a disconnect
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    def play(self, samples: np.ndarray, samplerate: int) -> Future[bool]:
        fut: Future[bool] = Future()
        if self._stream is None:
            fut.set_result(False)
            return fut
        data = resample(np.asarray(samples, dtype=np.float32), samplerate, self.samplerate)
        with self._lock:
            idle = not self._clips and time.monotonic() - self._last_sound > WAKE_AFTER_IDLE_S
            if idle:  # the output may have dozed off: give it a moment of silence to wake into
                data = np.concatenate([np.zeros(int(self.samplerate * WAKE_PAD_S), dtype=np.float32), data])
            self._clips.append(_Clip(data, fut))
        return fut

    def blip(self, samples: np.ndarray, samplerate: int) -> None:
        """Play a short sound now, over whatever is playing, without queueing or being cut by `stop`."""
        if self._stream is None:
            return
        data = resample(np.asarray(samples, dtype=np.float32), samplerate, self.samplerate)
        with self._lock:
            self._overlay.append(_Clip(data, Future()))

    def stop(self) -> None:
        """Cut off everything playing or queued (barge-in)."""
        with self._lock:
            clips, self._clips = list(self._clips), deque()
        for c in clips:
            if not c.future.done():
                c.future.set_result(False)

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._clips)

    def _callback(self, outdata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        finished: list[_Clip] = []
        with self._lock:
            while filled < frames and self._clips:
                clip = self._clips[0]
                take = min(frames - filled, len(clip.data) - clip.pos)
                out[filled : filled + take] = clip.data[clip.pos : clip.pos + take]
                clip.pos += take
                filled += take
                if clip.pos >= len(clip.data):
                    finished.append(self._clips.popleft())
            for clip in list(self._overlay):
                take = min(frames, len(clip.data) - clip.pos)
                out[:take] += clip.data[clip.pos : clip.pos + take]
                clip.pos += take
                if clip.pos >= len(clip.data):
                    self._overlay.remove(clip)
            if self._clips or filled:
                self._last_sound = time.monotonic()
        out *= self.volume
        self.level = float(np.sqrt(np.mean(out**2))) if frames else 0.0
        if self.keep_alive and filled < frames:  # keep a sleepy Bluetooth output awake between messages
            out[filled:] += self._noise.standard_normal(frames - filled).astype(np.float32) * KEEP_ALIVE_LEVEL
        outdata[:] = out[:, None] if outdata.shape[1] == 1 else np.repeat(out[:, None], outdata.shape[1], axis=1)
        for c in finished:
            if not c.future.done():
                c.future.set_result(True)

    def _finished(self) -> None:
        # The stream stopped on its own: device unplugged or driver reset.
        if self._stream is not None:
            self.error = "audio output stopped (device disconnected?)"
            log.warning(self.error)
            self.stop()


class NullPlayer(AudioPlayer):
    """Pretends to play (taking real time), for tests and machines without audio."""

    def open(self, device_name: str = "") -> None:
        self.device_name = device_name
        self.error = None
        self._stream = object()

    def close(self) -> None:
        self.stop()

    def play(self, samples: np.ndarray, samplerate: int) -> Future[bool]:
        fut: Future[bool] = Future()
        duration = len(samples) / max(1, samplerate)
        clip = _Clip(np.zeros(0, dtype=np.float32), fut)
        with self._lock:
            self._clips.append(clip)

        def finish() -> None:
            time.sleep(duration)
            with self._lock:
                if clip in self._clips:
                    self._clips.remove(clip)
            if not fut.done():
                fut.set_result(True)

        threading.Thread(target=finish, daemon=True).start()
        return fut
