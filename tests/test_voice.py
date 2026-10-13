import asyncio
import hashlib
import http.server
import threading
from pathlib import Path

import numpy as np
import pytest

from box_box.config import Settings
from box_box.engineer.radio import Priority, RadioCall
from box_box.voice import fx
from box_box.voice.models import ModelDownloader, ModelFile
from box_box.voice.normalize import for_speech
from box_box.voice.player import NullPlayer
from box_box.voice.speaker import Speaker
from box_box.voice.tts import TTS, Engine, SilentEngine, TTSUnavailable


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Lap 1:26.8, best so far.", "Lap one twenty-six eight, best so far."),
        ("Lap 1:05.3", "Lap one oh five three"),
        ("P6, gap 1.2", "P six, gap one point two"),
        ("FP1 at Monza", "F P one at Monza"),
        ("VSC, VSC", "V S C, V S C"),
        ("Tyres at 52 percent", "Tyres at fifty-two percent"),
        ("+0.3", "plus point three"),
    ],
)
def test_normalizer(text: str, spoken: str) -> None:
    assert for_speech(text) == spoken


@pytest.mark.parametrize("n", [0, 1, 50, 240, 359, 361])
def test_radio_effect_handles_tiny_clips(n: int) -> None:
    out = fx.radio(np.full(n, 0.2, dtype=np.float32), 24_000, 1.0, seed=1)
    assert not np.isnan(out).any()


def test_radio_effect_band_limits_and_stays_in_range() -> None:
    sr = 24_000
    t = np.arange(sr) / sr
    voice = (
        0.5 * np.sin(2 * np.pi * 150 * t) + 0.5 * np.sin(2 * np.pi * 1000 * t) + 0.3 * np.sin(2 * np.pi * 7000 * t)
    ).astype(np.float32)
    out = fx.radio(voice, sr, 1.0, beeps=False, seed=1)
    assert out.dtype == np.float32 and np.max(np.abs(out)) <= 1.0 and not np.isnan(out).any()
    spec = np.abs(np.fft.rfft(out)) ** 2
    f = np.fft.rfftfreq(len(out), 1 / sr)
    band = spec[(f > 300) & (f < 3400)].sum() / spec.sum()
    assert band > 0.9
    clean = fx.radio(voice, sr, 0.0, beeps=True, seed=1)
    assert len(clean) > len(voice)  # beeps and padding added


class FakeEngine(Engine):
    name = "kokoro"

    def __init__(self, fail_load: bool = False) -> None:
        self.fail_load = fail_load
        self.calls = 0

    def load(self) -> None:
        if self.fail_load:
            raise TTSUnavailable("no model")

    def synthesize(self, text, voice, speed):  # type: ignore[no-untyped-def]
        self.calls += 1
        return np.full(2400, 0.1, dtype=np.float32), 24_000


def test_tts_falls_back_and_caches() -> None:
    broken = TTS({"kokoro": FakeEngine(fail_load=True), "off": SilentEngine()})
    assert broken.choose("kokoro") == "off"
    engine = FakeEngine()
    tts = TTS({"kokoro": engine, "off": SilentEngine()})
    assert tts.choose("kokoro") == "kokoro" and tts.status == "ready"
    a = tts.submit("Copy.", "am_michael", 1.0).result(5)
    b = tts.submit("Copy.", "am_michael", 1.0).result(5)
    assert engine.calls == 1 and a[0] is b[0]


def test_speaker_plays_and_can_be_cut_off() -> None:
    tts = TTS({"kokoro": FakeEngine(), "off": SilentEngine()})
    tts.choose("kokoro")
    player = NullPlayer()
    player.open()
    settings = Settings()
    speaker = Speaker(tts, player, lambda: settings)

    async def scenario() -> tuple[bool, bool]:
        ok = await speaker.say(RadioCall("a", Priority.INFO, "Copy.", 0, 5))
        long = np.zeros(24_000 * 5, dtype=np.float32)
        player_play = player.play
        player.play = lambda samples, sr: player_play(long, 24_000)  # type: ignore[method-assign]
        task = asyncio.create_task(speaker.say(RadioCall("b", Priority.INFO, "Long one.", 0, 5)))
        await asyncio.sleep(0.3)
        speaker.stop()
        return ok, await task

    ok, cut = asyncio.run(scenario())
    assert ok is True and cut is False
    events = [e["type"] for e in speaker.bus.history]
    assert events.count("radio") == 2


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = b""
    fail_first = False
    hits = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits += 1
        data = self.payload
        start = 0
        rng = self.headers.get("Range")
        if type(self).fail_first and type(self).hits == 1:
            # Send half the file then drop the connection.
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data[: len(data) // 2])
            self.wfile.flush()
            self.connection.close()
            return
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # type: ignore[no-untyped-def]
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def test_downloader_resumes_and_verifies(server, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("box_box.voice.models.time.sleep", lambda s: None)
    payload = bytes(range(256)) * 400
    _Handler.payload, _Handler.fail_first, _Handler.hits = payload, True, 0
    url = f"http://127.0.0.1:{server.server_address[1]}/m.bin"
    good = ModelFile("m.bin", url, hashlib.sha256(payload).hexdigest(), len(payload))
    dl = ModelDownloader([good], tmp_path)
    dl.start()
    assert dl.wait(20) and (tmp_path / "m.bin").read_bytes() == payload
    assert dl.progress() == {"state": "ready", "error": None, "percent": 100.0}
    assert _Handler.hits == 2  # dropped once, resumed

    _Handler.fail_first, _Handler.hits = False, 0
    bad = ModelFile("bad.bin", url, "0" * 64, len(payload))
    dl2 = ModelDownloader([bad], tmp_path)
    dl2.start()
    assert not dl2.wait(20)
    prog = dl2.progress()
    assert prog["state"] == "error" and "corrupt" in prog["error"]
    assert not (tmp_path / "bad.bin").exists()


def test_tts_queue_prioritises_and_dedupes() -> None:
    import threading
    import time

    order: list[str] = []
    gate = threading.Event()

    class Slow(Engine):
        name = "kokoro"

        def synthesize(self, text, voice, speed):  # type: ignore[no-untyped-def]
            gate.wait(2)
            order.append(text)
            return np.zeros(10, dtype=np.float32), 24_000

    tts = TTS({"kokoro": Slow(), "off": SilentEngine()})
    tts.choose("kokoro")
    first = tts.submit("blocker", "v", 1.0, urgent=False)  # occupies the worker
    time.sleep(0.05)
    p1 = tts.submit("prefetch one", "v", 1.0, urgent=False)
    p2 = tts.submit("prefetch two", "v", 1.0, urgent=False)
    now = tts.submit("say this now", "v", 1.0)
    again = tts.submit("prefetch two", "v", 1.0)  # same text: shares the future, promoted
    assert again is p2
    gate.set()
    for f in (first, p1, p2, now):
        f.result(5)
    assert order[0] == "blocker" and order[1] in ("say this now", "prefetch two")
    assert order.index("say this now") < order.index("prefetch one")
    assert order.count("prefetch two") == 1
    tts.shutdown()


def test_names_are_said_the_paddock_way() -> None:
    from box_box.voice.pronounce import to_phonemes

    fake = lambda chunk: f"[{chunk.strip()}]"  # noqa: E731 - stands in for the voice's dictionary
    out = to_phonemes("Gap to Leclerc, 1.2. Ocon's closing at Eau Rouge.", fake)
    assert "ləklˈɛɹ," in out and "oʊkˈɔnz" in out and "ˌoʊ ɹˈuːʒ" in out
    assert out.startswith("[Gap to]") and "Leclerc" not in out


def test_long_pauses_are_tightened() -> None:
    import numpy as np

    from box_box.voice.tts import tighten

    sr = 24_000
    tone = 0.3 * np.sin(np.linspace(0, 400 * np.pi, sr // 2)).astype(np.float32)
    silence = np.zeros(sr // 2, dtype=np.float32)  # half a second of dead air between phrases
    out = tighten(np.concatenate([silence, tone, silence, tone, silence]), sr)
    assert len(out) < 1.4 * sr  # 2.5 s in, the dead air mostly gone
    assert len(out) > 0.95 * sr  # both phrases kept


def test_symbols_are_said_the_way_engineers_say_them() -> None:
    """From a real session: 'apex speed 5 km/h down' came out as 'kilometres slash h'."""
    assert "slash" not in for_speech("Apex speed 5 km/h down.") and "k p h" in for_speech("Apex speed 5 km/h down.")
    assert for_speech("Softs/mediums?") == "Softs or mediums?"
    assert for_speech("Turns 1 to 3 - front left will grain.") == "Turns one to three, front left will grain."
    assert for_speech("2.6s back at T14.") == "two point six seconds back at turn fourteen."
    assert "/" not in for_speech("50/50 call") and "1990s" in for_speech("like the 1990s")


def test_a_transmission_keys_up_talks_over_a_carrier_and_lets_go() -> None:
    sr = 24_000
    t = np.arange(int(sr * 0.5)) / sr
    voice = (0.6 * np.sin(2 * np.pi * 900 * t)).astype(np.float32)
    first = fx.radio(voice, sr, 0.8, seed=3, open_beep=True, close_beep=False)
    middle = fx.radio(voice, sr, 0.8, seed=3, open_beep=False, close_beep=False)
    assert len(first) > len(middle)  # key-up: click, beep and a moment of open channel before the voice
    # The open channel before the voice isn't dead silence: the carrier hiss is there.
    pre = first[int(sr * 0.12) : int(sr * 0.17)]
    assert 1e-4 < float(np.sqrt(np.mean(pre**2))) < 0.05
    clean = fx.radio(voice, sr, 0.0, beeps=False, seed=3)
    assert float(np.max(np.abs(clean[: int(sr * 0.02)]))) == 0.0  # no effect: no hiss either


def test_held_key_gaps_are_decided_per_call_and_never_on_urgent_calls() -> None:
    from box_box.voice.speaker import hold_split

    calls = [RadioCall("gaps", Priority.INFO, "Gap to Leclerc is 1.2. He's on older tyres.", 0, 10) for _ in range(40)]
    splits = [hold_split(c, "human") for c in calls]
    assert all(hold_split(c, "human") == s for c, s in zip(calls, splits, strict=True))  # prefetch == playback
    held = [s for s in splits if len(s[0]) == 2]
    assert held and len(held) < len(splits)
    assert held[0][0] == ["Gap to Leclerc is 1.2.", "He's on older tyres."] and 0.35 <= held[0][1] <= 0.8
    urgent = RadioCall("box", Priority.SAFETY, "Box, box. Box this lap.", 0, 10)
    assert hold_split(urgent, "human") == (["Box, box. Box this lap."], 0.0)
    assert hold_split(calls[0], "off") == ([calls[0].text], 0.0)


def test_driver_key_sounds() -> None:
    down, up = fx.driver_key(24_000, True, 0.5, seed=1), fx.driver_key(24_000, False, 0.5, seed=1)
    assert 0.05 < len(down) / 24_000 < 0.2 and 0.1 < len(up) / 24_000 < 0.3
    assert 0.02 < float(np.max(np.abs(down))) < 0.3


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Turns 1-3.", "Turns one to three."),
        ("50/50 call.", "fifty-fifty call."),
        ("+1 lap.", "plus one lap."),
        ("1:00.5", "one oh oh five"),
        ("Confirm on the MFD.", "Confirm on the M F D."),
        ("The LEADERS", "The LEADERS"),
    ],
)
def test_radio_text_edge_cases(text: str, spoken: str) -> None:
    assert for_speech(text) == spoken


def test_an_abandoned_synthesis_never_poisons_that_line() -> None:
    """A synth that timed out left a cancelled future behind, and that exact line never played again."""
    import threading

    gate = threading.Event()

    class Slow(Engine):
        name = "kokoro"

        def synthesize(self, text, voice, speed):  # type: ignore[no-untyped-def]
            gate.wait(5)
            return np.ones(10, dtype=np.float32), 24_000

    tts = TTS({"kokoro": Slow(), "off": SilentEngine()})
    tts.choose("kokoro")
    blocker = tts.submit("Something long.", "am_michael", 1.0)
    first = tts.submit("Box, box.", "am_michael", 1.0)
    first.cancel()  # the speaker gave up waiting
    gate.set()
    blocker.result(5)
    again = tts.submit("Box, box.", "am_michael", 1.0)
    assert not again.cancelled() and len(again.result(5)[0]) == 10
    tts.shutdown()


def test_stretch_changes_speed_not_pitch() -> None:
    from box_box.voice.tts import stretch

    sr = 24_000
    t = np.arange(sr * 2) / sr
    voice = (0.5 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    fast = stretch(voice, sr, 1.4)
    assert abs(len(voice) / len(fast) - 1.4) < 0.03
    f = np.fft.rfftfreq(len(fast), 1 / sr)
    assert abs(f[np.argmax(np.abs(np.fft.rfft(fast)))] - 300) < 3


def test_replies_sometimes_take_a_thinking_beat_at_a_comma() -> None:
    from box_box.voice.speaker import hold_split

    replies = [
        RadioCall(
            f"reply:{i}:0",
            Priority.STRATEGY,
            "You'd lose two places, out in P7, right behind Alonso.",
            0,
            10,
            source="reply",
        )
        for i in range(40)
    ]
    split = [hold_split(c, "human") for c in replies]
    beats = [s for s in split if len(s[0]) == 2]
    assert beats and len(beats) < len(split)
    assert all(p[0].endswith(",") and 0.18 <= gap <= 0.42 for p, gap in beats)
    assert all(len(hold_split(c, "off")[0]) == 1 for c in replies)
