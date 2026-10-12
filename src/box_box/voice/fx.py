"""The team-radio sound.

A real transmission isn't just a band-limited voice. The engineer keys the radio (a click, the beep,
and a moment of open carrier with the pit wall behind him), the channel opens up, he talks over a
constant noise floor, sometimes holds the key while he looks at the screen, then lets go: a short hiss
tail and the closing beep. The voice itself is narrow-band, heavily compressed and slightly crunchy
from the digital link.

`intensity` 0 leaves the voice studio-clean (beeps optional); 1 is a proper onboard radio. Pure numpy,
a few ms per second of audio, so it runs inline.
"""

from __future__ import annotations

import numpy as np

SR_DEFAULT = 24_000


# ---- building blocks ---------------------------------------------------------------------------------


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
    return np.fft.irfft(spec * hp * lp, size)[:n].astype(np.float32)


def _envelope(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    win = max(1, min(len(x), int(sr * ms / 1000)))  # never longer than the clip (np.convolve "same" grows)
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


def _crunch(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """The digital link: a lower effective sample rate (sample-and-hold) and a few less bits."""
    if amount <= 0 or len(x) == 0:
        return x
    hold = max(1, round(sr / (16_000 - 6_000 * amount)))  # 16 kHz clean-ish down to 10 kHz at full effect
    y = np.repeat(x[::hold], hold)[: len(x)]
    levels = 2 ** (12 - 4 * amount)  # 12 bits down to 8
    y = np.round(y * levels) / levels
    return ((1 - amount) * x + amount * y).astype(np.float32)


def _ramp(n: int, rising: bool) -> np.ndarray:
    r = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, max(n, 1), dtype=np.float32))
    return r if rising else r[::-1]


def _channel_sweep(x: np.ndarray, sr: int, i: float) -> np.ndarray:
    """The channel opening and closing: the first and last moments are narrower and quieter, so the voice
    swells in after the key-up and fades out on the release instead of starting and stopping dead."""
    n = len(x)
    head, tail = min(n // 3, int(sr * 0.09)), min(n // 3, int(sr * 0.12))
    if head < 8 or tail < 8 or i <= 0:
        return x
    narrow = _bandpass(x, sr, 700, 2200)
    y = x.copy()
    h = _ramp(head, rising=True)
    y[:head] = (1 - h) * narrow[:head] * 0.5 + h * x[:head]
    t = _ramp(tail, rising=False)
    y[-tail:] = (1 - t) * narrow[-tail:] * 0.35 + t * x[-tail:]
    return y


def carrier(n: int, sr: int, i: float, rng: np.random.Generator) -> np.ndarray:
    """The open channel: a hiss floor plus the pit wall faintly behind (a low swell, like cars passing)."""
    if n <= 0 or i <= 0:
        return np.zeros(max(n, 0), dtype=np.float32)
    hiss = _bandpass(rng.standard_normal(n).astype(np.float32), sr, 900, 5000)
    hiss = hiss / (np.std(hiss) + 1e-9) * 0.016 * i
    rumble = _bandpass(np.cumsum(rng.standard_normal(n)).astype(np.float32), sr, 320, 900)
    rumble = rumble / (np.std(rumble) + 1e-9)
    t = np.arange(n, dtype=np.float32) / sr
    swell = 0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(0.15, 0.4) * t + rng.uniform(0, 2 * np.pi))
    return (hiss + rumble * swell * 0.006 * i).astype(np.float32)


def tone(sr: int, freq: float, ms: float, level: float = 0.25) -> np.ndarray:
    n = int(sr * ms / 1000)
    t = np.arange(n) / sr
    env = np.minimum(1.0, np.minimum(t / 0.004, (n / sr - t) / 0.01))
    return (np.sin(2 * np.pi * freq * t) * env * level).astype(np.float32)


def click(sr: int, rng: np.random.Generator, level: float = 0.14) -> np.ndarray:
    """The key switch: a few ms of decaying noise."""
    n = int(sr * 0.008)
    return (rng.standard_normal(n) * np.exp(-np.arange(n) / (sr * 0.0015)) * level).astype(np.float32)


def _silence(sr: int, seconds: float) -> np.ndarray:
    return np.zeros(int(sr * seconds), dtype=np.float32)


# ---- the engineer's transmission ---------------------------------------------------------------------


def radio(
    samples: np.ndarray,
    sr: int,
    intensity: float,
    beeps: bool = True,
    seed: int | None = None,
    open_beep: bool = True,
    close_beep: bool = True,
) -> np.ndarray:
    """Process a mono float32 voice clip into a team-radio transmission.

    A reply spoken in several pieces is one transmission: only the first piece keys up (click, beep,
    a moment of carrier) and only the last lets go (hiss tail, beep); the pieces between are bridged by
    a short stretch of open carrier, so it sounds like he kept the button down."""
    rng = np.random.default_rng(seed)
    x = _normalize(np.asarray(samples, dtype=np.float32))
    i = float(np.clip(intensity, 0.0, 1.0))
    if i > 0 and len(x) >= 16:
        # Real F1 radio is band-limited and heavily compressed, with only a little grit. Strong overdrive
        # and crackle make a synthetic voice sound broken rather than "radio".
        low, high = 220 + 180 * i, 7000 - 3500 * i
        wet = _bandpass(x, sr, low, high)
        wet = _compress(wet, sr)
        drive = 1.0 + 1.6 * i
        wet = np.tanh(wet * drive) / np.tanh(drive)
        wet = _crunch(wet, sr, 0.6 * i)
        wet = _bandpass(wet, sr, low, high)  # the link's own filtering, after the crunch
        if i > 0.85:  # a weak-signal crackle only at the very top of the range
            crackles = rng.random(len(x)) < (i - 0.85) * 0.0004
            wet = wet + crackles * rng.standard_normal(len(x)).astype(np.float32) * 0.2
        x = (1 - i) * x + i * _normalize(wet, 0.8)
        x = _channel_sweep(x, sr, i)

    # Time on the open channel either side of the voice: longer on key-up/down, a short bridge between
    # pieces of one reply. Clean audio (intensity 0) gets only a breath of silence.
    lead = rng.uniform(0.12, 0.26) if open_beep else rng.uniform(0.03, 0.07)
    trail = rng.uniform(0.10, 0.20) if close_beep else rng.uniform(0.04, 0.08)
    if i <= 0:
        lead, trail = 0.03, 0.0
    body = np.concatenate([_silence(sr, lead), x.astype(np.float32), _silence(sr, trail)])
    body = body + carrier(len(body), sr, i, rng)
    if i > 0:  # the carrier itself fades in on key-up and out on release, never a hard edge
        edge = min(len(body) // 4, int(sr * 0.02))
        if edge > 1:
            if open_beep:
                body[:edge] *= _ramp(edge, rising=True)
            if close_beep:
                body[-edge:] *= _ramp(edge, rising=False)

    level = 0.4 + 0.6 * i
    parts: list[np.ndarray] = [_silence(sr, 0.02)]
    if open_beep:
        if i > 0:
            parts.append(click(sr, rng) * level)
        if beeps:
            parts += [_silence(sr, 0.015), tone(sr, 1800, 55) * level]
    parts.append(body)
    if close_beep:
        if beeps:
            parts += [tone(sr, 1200, 70) * level]
        if i > 0:
            parts += [_silence(sr, 0.01), click(sr, rng, 0.1) * level]
    out = np.concatenate(parts)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def hold_gap(sr: int, seconds: float) -> np.ndarray:
    """Dead air inside a transmission: he's still holding the key while he checks something. The carrier
    is added by `radio`, so this is just the silence it fills."""
    return _silence(sr, seconds)


# ---- the driver's own push-to-talk -------------------------------------------------------------------


def driver_key(sr: int, pressed: bool, intensity: float, seed: int | None = None) -> np.ndarray:
    """What the driver hears when he keys his own radio: a click and a short chirp up on press, a click,
    a chirp down and a breath of hiss on release. Quieter than the engineer's beeps."""
    rng = np.random.default_rng(seed)
    i = float(np.clip(intensity, 0.0, 1.0))
    level = 0.25 + 0.35 * i
    if pressed:
        parts = [click(sr, rng, 0.12), _silence(sr, 0.01), tone(sr, 1500, 35, 0.16), tone(sr, 2000, 30, 0.12)]
    else:
        hiss = _bandpass(rng.standard_normal(int(sr * 0.09)).astype(np.float32), sr, 900, 4000) * 0.03 * i
        hiss *= _ramp(len(hiss), rising=False)
        parts = [tone(sr, 1400, 30, 0.12), tone(sr, 1000, 40, 0.14), hiss, click(sr, rng, 0.1)]
    return (np.concatenate(parts) * level).astype(np.float32)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * sr_out / sr_in))
    positions = np.linspace(0, len(x) - 1, n_out)
    return np.interp(positions, np.arange(len(x)), x).astype(np.float32)
