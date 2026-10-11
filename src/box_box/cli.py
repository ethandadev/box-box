"""Command line: record sessions, replay them, and watch what the engineer would say.

box-box record                      # save a session from the game to recordings/
box-box watch                       # live: print the engineer's radio calls
box-box watch --file x.f1raw -s 4   # same, from a recording at 4x speed
box-box replay x.f1raw              # resend a recording over UDP, as if the game were running
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from box_box.engineer.radio import RadioCall
from box_box.engineer.traffic import TrafficDetector
from box_box.state.model import World
from box_box.telemetry import names
from box_box.telemetry.packets import Decoder
from box_box.telemetry.rawfile import RawWriter
from box_box.telemetry.sources import DEFAULT_PORT, PortUnavailable, bind_udp, file_source, udp_source


def _status_line(world: World) -> str:
    me = world.player
    if world.session is None or me is None or me.lap is None:
        return "waiting for session..."
    s = world.session
    lap = me.lap
    tyre = names.compound_name(me.status.visual_compound) if me.status else "?"
    return (
        f"{names.track_name(s.track_id)} {names.session_name(s.session_type)} | "
        f"P{lap.position} lap {lap.lap_num} {lap.lap_distance:6.0f}m | "
        f"{me.speed_kph:3.0f} kph | {tyre} | t={world.session_time:7.1f}"
    )


async def _watch(source: AsyncIterator[bytes], quiet: bool) -> None:
    decoder = Decoder()
    world = World()
    traffic = TrafficDetector()
    last_status = 0.0
    async for data in source:
        packet = decoder.decode(data)
        if packet is None:
            continue
        world.apply(packet)
        calls: list[RadioCall] = traffic.update(world)
        for event in world.drain_events():
            if not quiet:
                print(f"  [event {event.code}]")
        for call in calls:
            print(f"\n📻 [{call.priority.name}] {call.text}")
        if not quiet and time.monotonic() - last_status > 1.0:
            last_status = time.monotonic()
            print(_status_line(world), file=sys.stderr)


async def _record(sock: socket.socket, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    decoder = Decoder()
    world = World()
    start = time.monotonic_ns()
    last = 0.0
    with RawWriter(out) as writer:
        print(f"Recording UDP :{sock.getsockname()[1]} -> {out}  (Ctrl+C to stop)")
        try:
            async for data in udp_source(sock):
                writer.write(time.monotonic_ns() - start, data)
                packet = decoder.decode(data)
                if packet:
                    world.apply(packet)
                if time.monotonic() - last > 1.0:
                    last = time.monotonic()
                    writer.flush()
                    print(f"{writer.count:8d} packets | {_status_line(world)}", end="\r")
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


def _port_help(err: PortUnavailable) -> str:
    port = err.port
    return f"""
Can't listen for telemetry on UDP port {port} ({err.cause}).
Another program is already using it, or Windows has reserved it. Try, in order:

  1. Close other telemetry apps (SimHub, Crew Chief, F1 Laps, dashboards, another box-box window), run again.
  2. Find who has the port:
       netstat -ano -p udp | findstr :{port}
       tasklist /fi "PID eq <the number at the end of that line>"
  3. Check whether Windows reserved it (Hyper-V / WSL / Docker do this):
       netsh int ipv4 show excludedportrange protocol=udp
  4. Or just use another port: in F1 25 set Telemetry > UDP Port to {port + 1}, then run
       uv run box-box {{cmd}} --port {port + 1}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="box-box", description="AI race engineer for F1 25")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="record the game's UDP telemetry to an .f1raw file")
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
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    try:
        if args.cmd == "record":
            sock = bind_udp(args.port)
            out = args.out or Path("recordings") / f"{datetime.now():%Y%m%d-%H%M%S}.f1raw.gz"
            asyncio.run(_record(sock, out))
        elif args.cmd == "watch":
            source = file_source(args.file, args.speed) if args.file else udp_source(bind_udp(args.port))
            asyncio.run(_watch(source, args.quiet))
        elif args.cmd == "replay":
            asyncio.run(_replay(args.file, args.host, args.port, args.speed))
    except PortUnavailable as e:
        print(_port_help(e).replace("{cmd}", args.cmd), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
