"""Cloud voices that sound properly human: OpenAI, ElevenLabs and Google Gemini.

Kokoro is good for a local model but still sounds synthetic. These services are steerable or trained on
expressive speech, and on a race radio the difference is obvious. Each takes a key (OpenAI and Gemini
reuse the AI settings' keys), runs on the TTS worker thread, and returns 24 kHz mono audio. If a request
fails the TTS layer falls back to the local Kokoro voice for that line, so the radio never goes quiet.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import numpy as np

from box_box.config import Settings
from box_box.voice.tts import Audio, Engine, TTSUnavailable, stretch, tighten

log = logging.getLogger(__name__)

TIMEOUT_S = 6  # a radio line that takes longer is late anyway; Kokoro says it instead
STYLE_DEFAULT = (
    "You are an experienced Formula 1 race engineer talking to your driver on team radio during a session. "
    "Natural American accent. Calm, quick, clipped and confident; matter-of-fact, a little dry; short natural "
    "phrases with no drama, like a real person talking, not reading. "
    "Numbers and names said crisply. Raise urgency only for warnings like 'box box' or 'safety car'."
)

OPENAI_VOICES = ["ash", "onyx", "echo", "ballad", "sage", "verse", "alloy", "coral", "fable", "nova", "shimmer"]
GEMINI_VOICES = ["Charon", "Orus", "Fenrir", "Puck", "Iapetus", "Algenib", "Rasalgethi", "Alnilam", "Kore", "Aoede"]
ELEVENLABS_DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"  # "Adam", a stock voice every account has
ELEVENLABS_MAX_SPEED = 1.2  # the API's limit; anything faster is made up by time-stretching


def _pcm16(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def _paced(samples: np.ndarray, speed: float, native: float = 1.0) -> Audio:
    """At the driver's speed: whatever the service couldn't do itself (`native`) is made up by
    time-stretching (pitch unchanged), and long pauses are tightened like the local voice's."""
    sr = 24_000
    return tighten(stretch(samples, sr, speed / native), sr), sr


def _style(v_style: str, speed: float) -> str:
    style = v_style.strip() or STYLE_DEFAULT
    if speed >= 1.1:
        style += " Talk fast, like a busy engineer mid-race: no lingering on words, no gaps between phrases."
    return style


def _post(url: str, body: dict[str, Any], headers: dict[str, str], what: str) -> bytes:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 429:  # rate limited (Gemini's says "exceeded your current quota"): transient
            raise RuntimeError(f"{what} is rate limiting us: {detail[:120]}") from e
        if "quota" in detail.lower() or "credits" in detail.lower() or e.code == 402:
            raise TTSUnavailable(f"{what} is out of credits.") from e
        if e.code in (401, 403):
            raise TTSUnavailable(f"{what} rejected the key ({e.code}). Check it in Settings.") from e
        raise RuntimeError(f"{what} error {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"can't reach {what}: {e}") from e


class CloudEngine(Engine):
    label = "cloud"

    def __init__(self, settings: Callable[[], Settings]) -> None:
        self._settings = settings

    def key(self) -> str:
        raise NotImplementedError

    def load(self) -> None:
        if not self.key():
            raise TTSUnavailable(f"no {self.label} key (Settings > Voice)")


class OpenAIVoice(CloudEngine):
    """gpt-4o-mini-tts: takes plain-English directions on how to speak."""

    name, label = "openai", "OpenAI"
    URL = "https://api.openai.com/v1/audio/speech"

    def key(self) -> str:
        s = self._settings()
        return s.voice.openai_key.strip() or s.ai.openai_key.strip() or os.environ.get("OPENAI_API_KEY", "").strip()

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        v = self._settings().voice
        body = {
            "model": "gpt-4o-mini-tts",
            "voice": v.cloud_voice.strip() or "ash",
            "input": text,
            "instructions": _style(v.style, speed),
            "response_format": "pcm",  # 24 kHz, 16-bit, mono
        }
        raw = _post(self.URL, body, {"Authorization": f"Bearer {self.key()}"}, "OpenAI")
        return _paced(_pcm16(raw), speed)  # gpt-4o-mini-tts takes no speed: directed fast, then stretched


class ElevenLabsVoice(CloudEngine):
    """ElevenLabs Flash: very natural, low latency."""

    name, label = "elevenlabs", "ElevenLabs"
    URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_24000"

    def key(self) -> str:
        return self._settings().voice.elevenlabs_key.strip() or os.environ.get("ELEVENLABS_API_KEY", "").strip()

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        v = self._settings().voice
        body = {
            "text": text,
            "model_id": "eleven_flash_v2_5",
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.8,
                "style": 0.15,
                "speed": max(0.7, min(ELEVENLABS_MAX_SPEED, speed)),
            },
        }
        raw = _post(
            self.URL.format(voice=v.cloud_voice.strip() or ELEVENLABS_DEFAULT_VOICE),
            body,
            {"xi-api-key": self.key()},
            "ElevenLabs",
        )
        return _paced(_pcm16(raw), speed, native=max(0.7, min(ELEVENLABS_MAX_SPEED, speed)))


class GeminiVoice(CloudEngine):
    """Gemini 2.5 Flash TTS: natural, steerable with a style prompt, has a free tier."""

    name, label = "gemini", "Google Gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    MODELS = ["gemini-2.5-flash-tts", "gemini-2.5-flash-preview-tts"]  # newest first; the first that answers wins

    def key(self) -> str:
        s = self._settings()
        return (
            s.voice.gemini_key.strip()
            or s.ai.gemini_key.strip()
            or os.environ.get("GEMINI_API_KEY", "").strip()
            or os.environ.get("GOOGLE_API_KEY", "").strip()
        )

    def synthesize(self, text: str, voice: str, speed: float) -> Audio:
        v = self._settings().voice
        body = {
            "contents": [{"parts": [{"text": f"{_style(v.style, speed)}\nSay exactly this, nothing else: {text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v.cloud_voice.strip() or "Charon"}}
                },
            },
        }
        raw = b""
        for model in list(self.MODELS):
            try:
                raw = _post(self.URL.format(model=model), body, {"x-goog-api-key": self.key()}, "Gemini")
                if self.MODELS[0] != model:
                    self.MODELS.remove(model)
                    self.MODELS.insert(0, model)  # remember the one that works
                break
            except RuntimeError as e:
                if "error 404" not in str(e) or model == self.MODELS[-1]:
                    raise
        try:
            data = json.loads(raw)
            b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Gemini sent no audio: {raw[:160]!r}") from e
        return _paced(_pcm16(base64.b64decode(b64)), speed)


def cloud_engines(settings: Callable[[], Settings]) -> dict[str, Engine]:
    return {e.name: e for e in (OpenAIVoice(settings), ElevenLabsVoice(settings), GeminiVoice(settings))}
