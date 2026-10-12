"""Cloud voices against a fake HTTP server: request shape, audio decoding, and falling back when they fail."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from box_box.config import Settings
from box_box.voice import cloud
from box_box.voice.tts import TTS, Engine, SilentEngine, TTSUnavailable

PCM = (np.sin(np.linspace(0, 50, 2400)) * 12000).astype("<i2").tobytes()  # 0.1 s at 24 kHz


class Fake(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict, dict]] = []
    fail = False
    out_of_credits = False

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.requests.append((self.path, dict(self.headers), body))
        if Fake.out_of_credits:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "quota_exceeded", "message": "This request exceeds your quota."}}')
            return
        if Fake.fail:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        self.send_response(200)
        self.end_headers()
        if "generateContent" in self.path:
            payload = {
                "candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(PCM).decode()}}]}}]
            }
            self.wfile.write(json.dumps(payload).encode())
        else:
            self.wfile.write(PCM)

    def log_message(self, *a) -> None:  # type: ignore[no-untyped-def]
        pass


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setattr(cloud.OpenAIVoice, "URL", base + "/v1/audio/speech")
    monkeypatch.setattr(cloud.ElevenLabsVoice, "URL", base + "/v1/text-to-speech/{voice}?output_format=pcm_24000")
    monkeypatch.setattr(cloud.GeminiVoice, "URL", base + "/v1beta/models/{model}:generateContent")
    Fake.requests, Fake.fail, Fake.out_of_credits = [], False, False
    yield
    srv.shutdown()


def settings() -> Settings:
    s = Settings()
    s.ai.openai_key, s.ai.gemini_key, s.voice.elevenlabs_key = "sk-oa", "AIza", "el-key"
    s.voice.style = "calm and dry"
    return s


def test_each_cloud_voice_returns_audio(server) -> None:  # type: ignore[no-untyped-def]
    s = settings()
    engines = cloud.cloud_engines(lambda: s)
    for name in ("openai", "elevenlabs", "gemini"):
        engines[name].load()
        audio, sr = engines[name].synthesize("Box this lap.", "am_michael", 1.15)
        assert sr == 24_000 and len(audio) == 2400 and float(np.max(np.abs(audio))) > 0.3, name
    (p1, h1, b1), (p2, h2, b2), (p3, h3, b3) = Fake.requests
    assert (
        b1["model"] == "gpt-4o-mini-tts"
        and b1["instructions"] == "calm and dry"
        and h1["Authorization"] == "Bearer sk-oa"
    )
    assert "/pNInz6obpgDQGcFmaJgB" in p2 and h2["Xi-Api-Key"] == "el-key" and b2["text"] == "Box this lap."
    assert "Box this lap." in b3["contents"][0]["parts"][0]["text"] and h3["X-Goog-Api-Key"] == "AIza"


def test_missing_key_means_unavailable() -> None:
    s = Settings()
    with pytest.raises(TTSUnavailable):
        cloud.cloud_engines(lambda: s)["elevenlabs"].load()


def test_a_failing_cloud_voice_falls_back_to_kokoro(server) -> None:  # type: ignore[no-untyped-def]
    class FakeKokoro(Engine):
        name = "kokoro"

        def synthesize(self, text: str, voice: str, speed: float):  # type: ignore[no-untyped-def]
            return np.ones(100, dtype=np.float32) * 0.1, 24_000

    s = settings()
    tts = TTS({"kokoro": FakeKokoro(), "off": SilentEngine(), **cloud.cloud_engines(lambda: s)})
    assert tts.choose("openai") == "openai"
    Fake.fail = True
    audio, _ = tts.submit("Radio check.", "am_michael", 1.15).result(10)
    assert len(audio) == 100  # said with the local voice instead of going silent
    tts.shutdown()


def test_out_of_credits_switches_to_the_local_voice_for_good(server) -> None:  # type: ignore[no-untyped-def]
    """From a real session: ElevenLabs ran out of credits and every line waited on a refusal first."""
    speeds: list[float] = []

    class FakeKokoro(Engine):
        name = "kokoro"

        def synthesize(self, text: str, voice: str, speed: float):  # type: ignore[no-untyped-def]
            speeds.append(speed)
            return np.ones(100, dtype=np.float32) * 0.1, 24_000

    s = settings()
    tts = TTS({"kokoro": FakeKokoro(), "off": SilentEngine(), **cloud.cloud_engines(lambda: s)})
    assert tts.choose("elevenlabs") == "elevenlabs"
    Fake.out_of_credits = True
    for i in range(3):
        audio, _ = tts.submit(f"Line {i}.", "am_michael", 1.4).result(10)
        assert len(audio) == 100
    assert len(Fake.requests) == 1  # asked once, then straight to the local voice
    assert tts.degraded and "out of credits" in (tts.error or "")
    assert max(speeds) <= 1.2  # the stand-in voice doesn't suddenly rush
    tts.shutdown()


def test_voice_keys_work_without_the_ai_provider_and_stay_secret() -> None:
    """Chatting through Poe left no way to enter a Gemini key for the voice."""
    from box_box.config import SECRETS

    s = Settings()
    s.voice.gemini_key, s.voice.openai_key = "AIza-voice", "sk-voice"
    engines = cloud.cloud_engines(lambda: s)
    assert engines["gemini"].key() == "AIza-voice" and engines["openai"].key() == "sk-voice"
    assert ("voice", "gemini_key") in SECRETS and ("voice", "openai_key") in SECRETS
