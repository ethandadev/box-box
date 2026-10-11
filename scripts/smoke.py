"""End-to-end smoke test on real hardware paths (run in CI on Windows; also handy on a dev PC).

1. Download the Kokoro voice and synthesize a radio line with it.
2. Synthesize with the Windows SAPI voice (on Windows) on the TTS worker thread.
3. Load Whisper on the CPU and transcribe Kokoro's audio back to text: a full voice round-trip.
4. Start the real engine and web UI, stream a recording over UDP, check radio calls and the UI state.

Exits non-zero on any failure. Usage: uv run python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("BOXBOX_HOME", tempfile.mkdtemp(prefix="boxbox-smoke-"))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from box_box import config, logs  # noqa: E402
from box_box.app import Engine  # noqa: E402
from box_box.telemetry.rawfile import read_records  # noqa: E402
from box_box.ui.server import UIServer  # noqa: E402
from box_box.voice import fx  # noqa: E402
from box_box.voice.models import kokoro_files  # noqa: E402
from box_box.voice.stt import SpeechToText  # noqa: E402
from box_box.voice.tts import TTS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "race-2026-monza-finish.f1raw"
failures: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def voices() -> None:
    t = time.time()
    kokoro_files.start()
    ok = kokoro_files.wait(600)
    step("download Kokoro voice", ok, f"{time.time() - t:.0f}s {kokoro_files.progress()}")
    if not ok:
        return
    tts = TTS()
    step("load Kokoro", tts.choose("kokoro") == "kokoro", tts.status)
    t = time.time()
    samples, sr = tts.submit("Box box, box this lap. Gap to Leclerc is one point two.", "am_michael", 1.05).result(60)
    step("Kokoro synthesis", len(samples) > sr, f"{len(samples) / sr:.1f}s of audio in {time.time() - t:.1f}s")
    radio = fx.radio(samples, sr, 0.7)
    step("radio effect", len(radio) > len(samples) and float(abs(radio).max()) <= 1.0)

    if sys.platform == "win32":
        win = TTS()
        engine = win.choose("windows")
        try:
            t = time.time()
            w, wsr = win.submit("Radio check. Box box.", "", 1.0).result(60)
            step(
                "Windows SAPI voice",
                engine == "windows" and len(w) > wsr // 2,
                f"{len(w) / max(wsr, 1):.1f}s at {wsr} Hz in {time.time() - t:.1f}s",
            )
        except Exception as e:  # noqa: BLE001
            step("Windows SAPI voice", False, repr(e))

    stt = SpeechToText()
    t = time.time()
    stt.load("small.en", "cpu")
    step("load Whisper small.en (CPU)", stt.ready, f"{stt.status} in {time.time() - t:.0f}s {stt.error or ''}")
    if stt.ready:
        stt.extra_vocab = "Drivers: Leclerc, Norris, Verstappen, Hamilton. Track: Monza."  # as the app does per session
        sixteen = fx.resample(samples, sr, 16_000)
        tr = stt.transcribe(sixteen)
        heard = tr.text.lower()
        step(
            "voice round-trip (Kokoro -> Whisper)",
            "box" in heard and "leclerc" in heard.replace("le clerc", "leclerc"),
            repr(tr.text),
        )


async def engine_and_ui() -> None:
    store = config.SettingsStore()
    port = free_port()
    store.update(
        {
            "telemetry": {"port": port},
            "callouts": {"straights_only": False},
            "voice": {"engine": "windows" if sys.platform == "win32" else "off"},
        }
    )
    engine = Engine(store)
    await engine.start()
    server = UIServer(engine)
    ui_port = await server.start(18777)
    try:
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for i, (_, data) in enumerate(read_records(FIXTURE)):
            sender.sendto(data, ("127.0.0.1", port))
            if i % 10 == 0:
                await asyncio.sleep(0.002)
        await asyncio.sleep(3)
        state = await asyncio.to_thread(
            lambda: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/api/state", timeout=5).read())
        )
        session = state["status"]["session"]
        step("telemetry over UDP into the engine", engine.stats.packets > 100, f"{engine.stats.packets} packets")
        step("UI state shows the session", bool(session) and session["track"] == "Monza", json.dumps(session)[:120])
        html = await asyncio.to_thread(
            lambda: urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/", timeout=5).read().decode()
        )
        step("UI page served", "BOX&nbsp;BOX" in html)
        await engine.respond("radio check")
        lines: list[str] = []
        for _ in range(60):  # the voice may still be finishing an earlier call
            await asyncio.sleep(0.5)
            lines = [e["text"] for e in engine.bus.history if e["type"] == "radio"]
            if any("Loud and clear" in line for line in lines):
                break
        step("engineer answers", any("Loud and clear" in line for line in lines), "; ".join(lines)[:200])
        st = engine.status()
        print("      status:", json.dumps({k: st[k] for k in ("voice", "mic", "ptt")}, default=str)[:400])
    finally:
        await server.stop()
        await engine.stop()


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    logs.setup(verbose=False)
    print(f"Box Box smoke test on {sys.platform}, home {os.environ['BOXBOX_HOME']}", flush=True)
    voices()
    asyncio.run(engine_and_ui())
    print("\nFAILED: " + ", ".join(failures) if failures else "\nAll smoke checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
