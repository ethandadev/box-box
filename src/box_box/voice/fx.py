"""The team-radio sound: band-limited, compressed, a little crunchy, with a squelch beep on key-up and key-down.

`intensity` 0 leaves the voice studio-clean; 1 is a proper crackly onboard radio. Pure numpy, ~1 ms per
second of audio, so it runs inline.
"""

from __future__ import annotations

import numpy as np

SR_DEFAULT = 24_000


def _bandpass(x: np.ndarray, sr: int, low: float, high: float) -> np.ndarray:
    """Smooth band-pass in the frequency domain (4th-order Butterworth-like magnitude, zero phase)."""
    n = len(x)
    if n == 0:
        return x
    size = 1 << (n - 1).bit_length()
    spec = np.fft.rfft(x, size)
    f = np.fft.rfftfreq(size, 1.0 / sr)
    f[0] = 1e-3
    hp = 1.0 / np.sqrt(1.0 + (low / f) ** 8)
    lp = 1.0 / np.sqrt(1.0 + (f / high) ** 8)
    return np.fft.irfft(spec * hp * lp, size)[:n]


def _envelope(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    win = max(1, int(sr * ms / 1000))
    kernel = np.ones(win, dtype=np.float32) / win
    return np.convolve(np.abs(x), kernel, mode="same")


def _compress(x: np.ndarray, sr: int, threshold: float = 0.12, ratio: float = 4.0) -> np.ndarray:
    env = _envelope(x, sr, 15) + 1e-6
    gain = np.where(env > threshold, (env / threshold) ** (1.0 / ratio - 1.0), 1.0)
    y = x * gain
    peak = np.max(np.abs(y)) or 1.0
    return y / peak * 0.9


def _normalize(x: np.ndarray, peak: float = 0.7) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return x * (peak / m) if m > 1e-6 else x


def tone(sr: int, freq: float, ms: float, level: float = 0.25) -> np.ndarray:
    n = int(sr * ms / 1000)
    t = np.arange(n) / sr
    env = np.minimum(1.0, np.minimum(t / 0.004, (n / sr - t) / 0.01))
    return (np.sin(2 * np.pi * freq * t) * env * level).astype(np.float32)


def squelch_open(sr: int, rng: np.random.Generator) -> np.ndarray:
    click = (rng.standard_normal(int(sr * 0.012)) * 0.12).astype(np.float32)
    gap = np.zeros(int(sr * 0.02), dtype=np.float32)
    return np.concatenate([click, gap, tone(sr, 1800, 55), np.zeros(int(sr * 0.05), dtype=np.float32)])


def squelch_close(sr: int, rng: np.random.Generator) -> np.ndarray:
    tail = (rng.standard_normal(int(sr * 0.06)) * 0.05).astype(np.float32)
    tail *= np.linspace(1, 0, len(tail), dtype=np.float32)
    return np.concatenate([np.zeros(int(sr * 0.04), dtype=np.float32), tone(sr, 1200, 70), tail])


def radio(samples: np.ndarray, sr: int, intensity: float, beeps: bool = True, seed: int | None = None) -> np.ndarray:
    """Process a mono float32 voice clip into a team-radio transmission."""
    rng = np.random.default_rng(seed)
    x = _normalize(np.asarray(samples, dtype=np.float32))
    i = float(np.clip(intensity, 0.0, 1.0))
    if i > 0:
        low, high = 200 + 250 * i, 6500 - 3300 * i
        wet = _bandpass(x, sr, low, high)
        wet = _compress(wet, sr)
        drive = 1.0 + 4.0 * i
        wet = np.tanh(wet * drive) / np.tanh(drive)
        noise = _bandpass(rng.standard_normal(len(x)).astype(np.float32), sr, 800, 4000) * 0.02 * i
        wet = wet + noise
        if i > 0.5:  # occasional crackle, like a weak signal
            crackles = rng.random(len(x)) < (i - 0.5) * 0.0004
            wet = wet + crackles * rng.standard_normal(len(x)) * 0.3
        x = (1 - i) * x + i * _normalize(wet, 0.8)
    parts = [np.zeros(int(sr * 0.03), dtype=np.float32)]
    if beeps:
        parts.append(squelch_open(sr, rng) * (0.4 + 0.6 * i))
    parts.append(x.astype(np.float32))
    if beeps:
        parts.append(squelch_close(sr, rng) * (0.4 + 0.6 * i))
    out = np.concatenate(parts)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * sr_out / sr_in))
    positions = np.linspace(0, len(x) - 1, n_out)
    return np.interp(positions, np.arange(len(x)), x).astype(np.float32)
