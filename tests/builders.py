"""Build synthetic UDP packets byte-for-byte, so scenarios (traffic, flags...) can be tested without the game."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from box_box.telemetry.packets import HEADER, LAYOUTS, PacketId

FMT = 2026
UID = 0xB0B0B0B0


def header(pid: int, *, fmt: int = FMT, t: float = 0.0, frame: int = 0, player: int = 0, uid: int = UID) -> bytes:
    return HEADER.pack(fmt, 25, 1, 0, 1, pid, uid, t, frame, frame, player, 255)


def lap_slot(
    *,
    lap_distance: float = 0.0,
    lap_num: int = 1,
    position: int = 1,
    driver_status: int = 4,
    result_status: int = 2,
    pit_status: int = 0,
    last_lap_ms: int = 0,
    current_lap_ms: int = 0,
    lap_invalid: int = 0,
) -> tuple:
    return (
        last_lap_ms, current_lap_ms,
        0, 0, 0, 0,  # sector 1, sector 2
        0, 0, 0, 0,  # delta to car in front, delta to leader
        lap_distance, lap_distance, 0.0,
        position, lap_num, pit_status, 0, 0, lap_invalid, 0, 0, 0, 0, 0, position,
        driver_status, result_status, 0,
        0, 0, 0, 0.0, 255,
    )  # fmt: skip


def telemetry_slot(*, speed: int = 0, throttle: float = 0.0, brake: float = 0.0, steer: float = 0.0) -> tuple:
    return (
        speed, throttle, steer, brake, 0, 7, 11000, 0, 50, 0,
        500, 500, 600, 600, 90, 90, 92, 92, 100, 100, 102, 102,
        110, 23.0, 23.0, 22.5, 22.5, 0, 0, 0, 0,
    )  # fmt: skip


def per_car(pid: PacketId, slots: list[tuple], *, fmt: int = FMT, trailer: bytes = b"", **hdr) -> bytes:
    lay = LAYOUTS[fmt]
    s = {
        PacketId.LAP_DATA: lay.lap_data,
        PacketId.CAR_TELEMETRY: lay.car_telemetry,
        PacketId.PARTICIPANTS: lay.participant,
    }[pid]
    body = b"".join(s.pack(*v) for v in slots)
    body += bytes(s.size * (lay.max_cars - len(slots)))
    if pid == PacketId.PARTICIPANTS:
        body = bytes([len(slots)]) + body
    data = header(pid, fmt=fmt, **hdr) + body + trailer
    assert len(data) == lay.packet_sizes[pid], (pid, len(data))
    return data


def lap_packet(slots: list[tuple], **hdr) -> bytes:
    return per_car(PacketId.LAP_DATA, slots, trailer=b"\xff\xff", **hdr)


def telemetry_packet(slots: list[tuple], **hdr) -> bytes:
    return per_car(PacketId.CAR_TELEMETRY, slots, trailer=b"\xff\xff\x00", **hdr)


def participants_packet(names: list[str], *, fmt: int = FMT, **hdr) -> bytes:
    slots = []
    for i, name in enumerate(names):
        raw = name.encode()[:31].ljust(32, b"\x00")
        if fmt >= 2026:
            slots.append((1, i, 0, 476 + i % 11, 0, i + 1, 1, raw, 1, 0, 0, 0, 0) + (0,) * 12)
        else:
            slots.append((1, i, 0, i % 10, 0, i + 1, 1, raw, 1, 0, 0, 0, 0) + (0,) * 12)
    return per_car(PacketId.PARTICIPANTS, slots, fmt=fmt, **hdr)


def session_packet(
    *, track_length: int = 5000, session_type: int = 1, track_id: int = 7, fmt: int = FMT, **hdr
) -> bytes:
    size = LAYOUTS[fmt].packet_sizes[PacketId.SESSION]
    buf = bytearray(header(PacketId.SESSION, fmt=fmt, **hdr) + bytes(size - HEADER.size))
    struct.pack_into(
        "<BbbBHBbBHHBBBBBB", buf, HEADER.size, 0, 30, 22, 0, track_length, session_type, track_id, 0, 3600, 3600, 80,
        0, 0, 0, 0, 0,
    )  # fmt: skip
    return bytes(buf)


@dataclass
class Car:
    """One car in a scenario. Distances in metres, speed in km/h."""

    name: str
    dist: float
    speed: int
    status: int = 4  # DriverStatus
    pit: int = 0


@dataclass
class Scenario:
    """Feeds a World a consistent frame (lap data + telemetry) for a list of cars; car 0 is the player."""

    cars: list[Car]
    track_length: int = 5000
    session_type: int = 1
    t: float = 0.0
    frames: list[list[bytes]] = field(default_factory=list)

    def setup(self) -> list[bytes]:
        return [
            session_packet(track_length=self.track_length, session_type=self.session_type, t=self.t),
            participants_packet([c.name for c in self.cars], t=self.t),
        ]

    def frame(self) -> list[bytes]:
        laps = [
            lap_slot(
                lap_distance=c.dist % self.track_length,
                position=i + 1,
                driver_status=c.status,
                pit_status=c.pit,
            )
            for i, c in enumerate(self.cars)
        ]
        tel = [telemetry_slot(speed=c.speed) for c in self.cars]
        return [lap_packet(laps, t=self.t), telemetry_packet(tel, t=self.t)]

    def step(self, dt: float) -> None:
        self.t += dt
        for c in self.cars:
            c.dist += c.speed / 3.6 * dt
