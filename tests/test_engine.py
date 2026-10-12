import asyncio
import socket
from pathlib import Path

import numpy as np
import pytest

from box_box import config
from box_box.app import Engine
from box_box.diagnostics import report, run_checks
from box_box.memory.store import Memory
from box_box.telemetry.rawfile import read_records
from box_box.voice.player import NullPlayer
from box_box.voice.stt import SpeechToText, Transcript
from box_box.voice.tts import TTS, SilentEngine
from box_box.voice.tts import Engine as TTSEngine

ROOT = Path(__file__).parent.parent
PRACTICE = ROOT / "recordings" / "20261011-215319.f1raw.gz"
FIXTURE = Path(__file__).parent / "fixtures" / "race-2026-monza-finish.f1raw"


class QuickVoice(TTSEngine):
    name = "kokoro"

    def synthesize(self, text, voice, speed):  # type: ignore[no-untyped-def]
        return np.zeros(240, dtype=np.float32), 24_000  # 10 ms


class FakeSTT(SpeechToText):
    def __init__(self, text: str) -> None:
        super().__init__()
        self._model = object()
        self.status = "ready"
        self.text = text

    def load(self, model="auto", device="auto"):  # type: ignore[no-untyped-def]
        pass

    def transcribe(self, audio):  # type: ignore[no-untyped-def]
        return Transcript(self.text, len(audio) / 16000, 0.01)


class FakeMic:
    ok = True
    error = None
    level = 0.0
    recording = False

    def open(self, device_name=""):  # type: ignore[no-untyped-def]
        pass

    def close(self) -> None:
        pass

    def start_recording(self) -> None:
        self.recording = True

    def stop_recording(self):  # type: ignore[no-untyped-def]
        self.recording = False
        return np.ones(16000, dtype=np.float32) * 0.1


class Installed:
    def installed(self) -> bool:
        return True

    def start(self) -> None:
        pass

    def progress(self) -> dict:
        return {"state": "ready", "error": None, "percent": 100.0}


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_engine(tmp_path: Path, stt_text: str = "what's the gap", **settings_patch) -> Engine:
    store = config.SettingsStore(tmp_path / "settings.json")
    patch = {
        "telemetry": {"port": free_port(), "recordings_dir": str(tmp_path / "rec")},
        "callouts": {"straights_only": False},
    }
    for k, v in settings_patch.items():
        patch.setdefault(k, {}).update(v)
    store.update(patch)
    tts = TTS({"kokoro": QuickVoice(), "off": SilentEngine()})
    return Engine(
        store,
        player=NullPlayer(),
        tts=tts,
        stt=FakeSTT(stt_text),
        mic=FakeMic(),  # type: ignore[arg-type]
        memory=Memory(tmp_path / "m.sqlite3"),
        enable_ptt=True,
        downloader=Installed(),
    )


def radio_lines(engine: Engine) -> list[str]:
    return [e["text"] for e in engine.bus.history if e["type"] == "radio"]


@pytest.mark.skipif(not PRACTICE.exists(), reason="practice recording not present")
def test_practice_session_end_to_end_over_udp(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)

    async def scenario() -> None:
        await engine.start()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        port = engine.settings.telemetry.port
        for i, (_, data) in enumerate(read_records(PRACTICE)):
            sender.sendto(data, ("127.0.0.1", port))
            if i % 15 == 0:
                await asyncio.sleep(0.001)  # let the engine keep up, like a real 60 Hz stream
        await asyncio.sleep(5)  # the director spaces calls ~4 s apart; a 6 min session replays in ~3 s
        await engine.stop()

    asyncio.run(scenario())
    lines = radio_lines(engine)
    assert any("radio check" in line.lower() and ("Melbourne" in line or "Ethan" in line) for line in lines), lines
    assert any("Albon" in line and "pits" in line for line in lines), lines
    queued = [e["text"] for e in engine.bus.history if e["type"] == "queued"]
    assert any("1:26.8" in q and "best" in q.lower() for q in queued), queued
    # We fire 6 minutes of telemetry in ~3 s, so UDP drops some; the real game sends ~300 packets/s.
    assert engine.stats.packets > 10_000
    # The live session was recorded and named after the track.
    recs = list((tmp_path / "rec").glob("*.f1raw.gz"))
    assert recs and "Melbourne FP1" in recs[0].name or engine.recorder.active


def test_push_to_talk_question_gets_spoken_answer(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, stt_text="radio check")

    async def scenario() -> None:
        await engine.start()
        engine.on_ptt(True)  # an accidental tap: ignored
        await asyncio.sleep(0.05)
        engine.on_ptt(False)
        await asyncio.sleep(0.5)
        assert not any(e["type"] == "driver" for e in engine.bus.history)
        engine.on_ptt(True)
        await asyncio.sleep(0.5)
        engine.on_ptt(False)
        await asyncio.sleep(1.5)
        await engine.stop()

    asyncio.run(scenario())
    assert radio_lines(engine).count("Loud and clear, Ethan.") == 1
    kinds = [e["type"] for e in engine.bus.history]
    assert "driver" in kinds


def test_voice_commands_change_settings_and_typed_questions_work(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)

    async def scenario() -> None:
        await engine.start()
        for _, data in read_records(FIXTURE):
            engine.ingest(data)
        await engine.respond("less radio please")
        await engine.respond("what's the gap")
        await asyncio.sleep(3.0)  # transmissions include key-up and the hiss tail
        await engine.stop()

    asyncio.run(scenario())
    assert engine.settings.engineer.verbosity == "quiet"
    lines = radio_lines(engine)
    assert "Copy. Keeping it to the essentials." in lines
    assert any("ahead" in line or "behind" in line or "leading" in line.lower() for line in lines), lines


def test_bad_port_is_reported_not_fatal(tmp_path: Path) -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    engine = make_engine(tmp_path)
    engine.store.update({"telemetry": {"port": blocker.getsockname()[1]}})

    async def scenario() -> list[dict]:
        await engine.start()
        checks = run_checks(engine)
        text = report(engine)
        await engine.stop()
        assert "Box Box" in text and "Checks:" in text
        return checks

    try:
        checks = asyncio.run(scenario())
    finally:
        blocker.close()
    telemetry = next(c for c in checks if c["id"] == "telemetry")
    assert telemetry["status"] == "error" and "in use" in telemetry["detail"]


def test_status_snapshot_is_json_safe(tmp_path: Path) -> None:
    import json

    engine = make_engine(tmp_path)

    async def scenario() -> dict:
        await engine.start()
        for _, data in read_records(FIXTURE):
            engine.ingest(data)
        st = engine.status()
        await engine.stop()
        return st

    st = asyncio.run(scenario())
    json.dumps(st)
    assert st["session"]["track"] == "Monza" and st["player"]["position"] >= 1


def test_stuck_push_to_talk_is_released_with_a_warning(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)

    async def scenario() -> None:
        await engine.start()
        engine.on_ptt(True)
        engine._ptt_pressed_at -= 31  # held for over 30 s
        engine._ptt_watchdog()
        await engine.stop()

    asyncio.run(scenario())
    assert not engine.director.ptt_down
    assert any("stuck" in e.get("message", "") for e in engine.bus.history if e["type"] == "warning")
