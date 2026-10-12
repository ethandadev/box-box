"""Command line.

box-box                     start the engineer and open the UI (what the desktop shortcut runs)
box-box doctor              check the setup without starting anything
box-box watch [-f FILE]     print what the engineer would say, live or from a recording
box-box record              save the game's telemetry to a file
box-box replay FILE         resend a recording over UDP, as if the game were running
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
import time
import urllib.request
import webbrowser
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from box_box import __version__, logs
from box_box.telemetry.sources import DEFAULT_PORT, PortUnavailable, bind_udp, file_source, udp_source

log = logging.getLogger("box_box")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


# ---- run: the app -------------------------------------------------------------------------------


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.5) as resp:
            return resp.status == 200 and "version" in json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


async def _run(open_browser: bool) -> None:
    from box_box.app import Engine
    from box_box.config import SettingsStore
    from box_box.ui.server import UIServer

    store = SettingsStore()
    engine = Engine(store)
    server = UIServer(engine)
    await engine.start()
    port = await server.start(store.settings.ui.port)
    url = f"http://127.0.0.1:{port}/" + ("" if store.settings.onboarded else "#setup")
    print(
        f"\n  Box Box {__version__} is running.\n  Open {url} if your browser didn't.\n  Close this window (or press Ctrl+C) to stop.\n"
    )
    if open_browser and store.settings.ui.open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()
        await engine.stop()


def run(open_browser: bool = True, verbose: bool = False) -> int:
    logs.setup(verbose)
    from box_box.config import SettingsStore

    ui_port = SettingsStore().settings.ui.port
    if _already_running(ui_port):
        print("Box Box is already running; opening it.")
        webbrowser.open(f"http://127.0.0.1:{ui_port}/")
        return 0
    try:
        asyncio.run(_run(open_browser))
    except KeyboardInterrupt:
        print("Stopped.")
    except Exception:
        log.exception("Box Box crashed")
        print("\nBox Box hit an unexpected error. The log is in the Diagnostics folder:")
        from box_box import paths

        print(f"  {paths.logs_dir()}\n")
        return 1
    return 0


# ---- doctor -------------------------------------------------------------------------------------


def doctor() -> int:
    from box_box import diagnostics, paths
    from box_box.config import SettingsStore
    from box_box.voice.models import kokoro_files
    from box_box.voice.player import list_devices

    ok = True

    def line(status: str, label: str, detail: str) -> None:
        nonlocal ok
        mark = {"ok": "OK  ", "warn": "WARN", "error": "FAIL"}[status]
        ok &= status != "error"
        print(f"  [{mark}] {label}: {detail}")

    print(f"Box Box {__version__} doctor\n")
    line("ok" if sys.version_info[:2] in ((3, 12), (3, 13)) else "warn", "Python", sys.version.split()[0])
    for module in ("numpy", "aiohttp", "sounddevice", "kokoro_onnx", "faster_whisper", "pygame", "pynput", "anthropic"):
        try:
            __import__(module)
            line("ok", f"module {module}", "installed")
        except Exception as e:  # noqa: BLE001
            line("error" if module in ("numpy", "aiohttp") else "warn", f"module {module}", str(e).splitlines()[0])
    s = SettingsStore().settings
    try:
        bind_udp(s.telemetry.port).close()
        line("ok", "UDP port", f"{s.telemetry.port} is free")
    except PortUnavailable as e:
        line("error", "UDP port", f"{s.telemetry.port} unavailable: {e.cause} (another telemetry app running?)")
    outs, ins = list_devices("output"), list_devices("input")
    line("ok" if outs else "error", "audio outputs", ", ".join(d.name for d in outs[:4]) or "none found")
    line("ok" if ins else "warn", "microphones", ", ".join(d.name for d in ins[:4]) or "none found")
    line(
        "ok" if kokoro_files.installed() else "warn",
        "voice model",
        "installed" if kokoro_files.installed() else "not downloaded yet (happens on first start)",
    )
    gpu = diagnostics.gpu_name()
    line("ok", "GPU", gpu or "no NVIDIA GPU (speech recognition will use the CPU)")
    problem = s.ai_problem if s.ai.enabled else "off"
    line(
        "warn" if problem else "ok",
        "AI conversation",
        f"{s.provider.label}: " + (f"{problem} (common questions only)" if problem else "configured"),
    )
    line("ok", "app folder", str(paths.app_home()))
    print("\nAll good." if ok else "\nSome checks failed; see above.")
    return 0 if ok else 1


# ---- watch / record / replay (developer tools) ----------------------------------------------------


async def _watch(source: AsyncIterator[bytes], quiet: bool) -> None:
    from box_box.config import Settings
    from box_box.engineer.base import Context
    from box_box.engineer.detectors import all_detectors
    from box_box.engineer.radio import Phrasebook
    from box_box.state.model import World
    from box_box.telemetry import names
    from box_box.telemetry.packets import Decoder, PacketId

    decoder, world, detectors = Decoder(Decoder.DEFAULT_WANTED), World(), all_detectors()
    settings, say = Settings(), Phrasebook()
    last_status = 0.0
    async for data in source:
        packet = decoder.decode(data)
        if packet is None:
            continue
        world.apply(packet)
        if packet.id != PacketId.LAP_DATA:
            continue
        ctx = Context(world, world.drain_changes(), world.drain_events(), settings, say)
        for detector in detectors:
            for call in detector.update(ctx):
                print(f"📻 {world.session_time:7.1f}s [{call.priority.name:8}] {call.text}")
        if not quiet and time.monotonic() - last_status > 1.0:
            last_status = time.monotonic()
            me = world.player
            if world.session and me and me.lap:
                print(
                    f"   {names.track_name(world.session.track_id)} {names.session_name(world.session_type)} | "
                    f"P{me.lap.position} lap {me.lap.lap_num} | {me.speed_kph:3.0f} km/h",
                    file=sys.stderr,
                )
            else:
                print("   waiting for a session...", file=sys.stderr)


async def _record(sock: socket.socket, out: Path) -> None:
    from box_box.telemetry.rawfile import RawWriter

    out.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic_ns()
    last = 0.0
    with RawWriter(out) as writer:
        print(f"Recording UDP :{sock.getsockname()[1]} -> {out}  (Ctrl+C to stop)")
        try:
            async for data in udp_source(sock):
                writer.write(time.monotonic_ns() - start, data)
                if time.monotonic() - last > 1.0:
                    last = time.monotonic()
                    writer.flush()
                    print(f"{writer.count:8d} packets", end="\r")
        finally:
            writer.close()
            mb = out.stat().st_size / 1e6
            print(f"\nSaved {writer.count} packets to {out} ({mb:.1f} MB)")
            if mb > 95:
                print("Too big for GitHub (100 MB limit): set UDP Send Rate to 20Hz or record a shorter session.")


async def _replay(path: Path, host: str, port: int, speed: float) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = 0
    async for data in file_source(path, speed):
        sock.sendto(data, (host, port))
        n += 1
    print(f"Sent {n} packets to {host}:{port}")


def _port_help(err: PortUnavailable, cmd: str) -> str:
    port = err.port
    return f"""
Can't listen for telemetry on UDP port {port} ({err.cause}).
Another program is already using it, or Windows has reserved it. Try, in order:

  1. Close other telemetry apps (SimHub, Crew Chief, F1 Laps, dashboards, another Box Box window), run again.
  2. Find who has the port:
       netstat -ano -p udp | findstr :{port}
       tasklist /fi "PID eq <the number at the end of that line>"
  3. Check whether Windows reserved it (Hyper-V / WSL / Docker do this):
       netsh int ipv4 show excludedportrange protocol=udp
  4. Or just use another port: in F1 25 set Telemetry > UDP Port to {port + 1}, then run
       uv run box-box {cmd} --port {port + 1}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="box-box", description="Box Box: an AI race engineer for F1 25")
    parser.add_argument("-v", "--verbose", action="store_true", help="more logging")
    parser.add_argument("--version", action="version", version=f"box-box {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="start the engineer and the UI (default)")
    run_p.add_argument("--no-browser", action="store_true", help="don't open the browser")

    sub.add_parser("doctor", help="check the setup without starting anything")

    rec = sub.add_parser("record", help="record the game's UDP telemetry to an .f1raw.gz file")
    rec.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    rec.add_argument("-o", "--out", type=Path)

    watch = sub.add_parser("watch", help="print the engineer's radio calls, live or from a recording")
    watch.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    watch.add_argument("-f", "--file", type=Path, help="read a recording instead of listening")
    watch.add_argument("-s", "--speed", type=float, default=1.0, help="replay speed (0 = flat out)")
    watch.add_argument("-q", "--quiet", action="store_true", help="radio calls only")

    rep = sub.add_parser("replay", help="resend a recording over UDP as if the game were running")
    rep.add_argument("file", type=Path)
    rep.add_argument("--host", default="127.0.0.1")
    rep.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    rep.add_argument("-s", "--speed", type=float, default=1.0)

    args = parser.parse_args(argv)
    cmd = args.cmd or "run"
    if cmd == "run":
        return run(open_browser=not getattr(args, "no_browser", False), verbose=args.verbose)
    if cmd == "doctor":
        logging.basicConfig(level=logging.ERROR)
        return doctor()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        if cmd == "record":
            sock = bind_udp(args.port)
            out = args.out or Path("recordings") / f"{datetime.now():%Y%m%d-%H%M%S}.f1raw.gz"
            asyncio.run(_record(sock, out))
        elif cmd == "watch":
            source = file_source(args.file, args.speed) if args.file else udp_source(bind_udp(args.port))
            asyncio.run(_watch(source, args.quiet))
        elif cmd == "replay":
            asyncio.run(_replay(args.file, args.host, args.port, args.speed))
    except PortUnavailable as e:
        print(_port_help(e, cmd), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
