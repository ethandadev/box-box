"""The engineer's pit-wall screens: one live picture of the session, built up from decoded packets.

Everything downstream (detectors, the LLM's tools) reads from `World`; nothing else touches packets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from box_box.telemetry import names
from box_box.telemetry.packets import (
    CarDamage,
    CarMotion,
    CarStatus,
    CarTelemetry,
    CarTelemetry2,
    DriverStatus,
    Event,
    LapData,
    Packet,
    PacketId,
    Participant,
    ResultStatus,
    Session,
)

log = logging.getLogger(__name__)


@dataclass
class CarState:
    index: int
    participant: Participant | None = None
    lap: LapData | None = None
    telemetry: CarTelemetry | None = None
    status: CarStatus | None = None
    damage: CarDamage | None = None
    telemetry2: CarTelemetry2 | None = None
    motion: CarMotion | None = None
    best_lap_ms: int | None = None

    @property
    def name(self) -> str:
        """How the engineer refers to this driver on the radio."""
        if self.participant and self.participant.name:
            return names.surname(self.participant.name)
        return f"car {self.index}"

    @property
    def team(self) -> str | None:
        return names.team_name(self.participant.team_id) if self.participant else None

    @property
    def active(self) -> bool:
        return self.lap is not None and self.lap.result_status == ResultStatus.ACTIVE

    @property
    def on_track(self) -> bool:
        """Running on the circuit: active, out of the garage, not in the pit lane."""
        lap = self.lap
        return (
            lap is not None
            and lap.result_status == ResultStatus.ACTIVE
            and lap.driver_status != DriverStatus.IN_GARAGE
            and lap.pit_status == 0
        )

    @property
    def speed_kph(self) -> float:
        return float(self.telemetry.speed_kph) if self.telemetry else 0.0

    @property
    def driver_status(self) -> DriverStatus | None:
        return DriverStatus(self.lap.driver_status) if self.lap else None


@dataclass
class World:
    """Live session state. Feed it every decoded packet with `apply`."""

    session_uid: int = 0
    packet_format: int = 0
    session: Session | None = None
    session_time: float = 0.0
    player_index: int = 0
    num_active_cars: int = 0
    cars: list[CarState] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)  # since the last `drain_events`
    # Bumped whenever time jumps backwards (flashback, save reload), so detectors can drop their memory.
    timeline: int = 0
    session_started: bool = False

    # ---- queries -------------------------------------------------------------------------------

    @property
    def player(self) -> CarState | None:
        if 0 <= self.player_index < len(self.cars):
            return self.cars[self.player_index]
        return None

    @property
    def track_length(self) -> float:
        return float(self.session.track_length) if self.session else 0.0

    @property
    def session_type(self) -> int:
        return self.session.session_type if self.session else 0

    @property
    def is_practice(self) -> bool:
        return self.session_type in names.PRACTICE_SESSIONS

    @property
    def is_qualifying(self) -> bool:
        return self.session_type in names.QUALI_SESSIONS

    @property
    def is_race(self) -> bool:
        return self.session_type in names.RACE_SESSIONS

    def others(self) -> list[CarState]:
        return [c for c in self.cars if c.index != self.player_index and c.lap is not None]

    def by_position(self) -> list[CarState]:
        running = [c for c in self.cars if c.lap is not None and c.lap.position > 0 and c.lap.result_status >= 2]
        return sorted(running, key=lambda c: c.lap.position)  # type: ignore[union-attr]

    def drain_events(self) -> list[Event]:
        out, self.events = self.events, []
        return out

    # ---- updates -------------------------------------------------------------------------------

    def apply(self, packet: Packet) -> None:
        h = packet.header
        if h.session_uid == 0:
            return  # menu traffic
        if h.session_uid != self.session_uid:
            if packet.id != PacketId.SESSION:
                return  # wait for the Session packet before trusting a new session
            self._reset(h.session_uid, h.packet_format)
        if h.session_time + 0.5 < self.session_time:
            self.timeline += 1  # flashback or reload rewound the clock
        self.session_time = h.session_time
        self.player_index = h.player_car_index
        self._ensure_cars(packet)

        match packet.id:
            case PacketId.SESSION:
                self.session = packet.data  # type: ignore[assignment]
            case PacketId.LAP_DATA:
                for car, lap in zip(self.cars, packet.data, strict=False):  # type: ignore[call-overload]
                    self._update_lap(car, lap)
            case PacketId.PARTICIPANTS:
                self.num_active_cars = packet.num_active_cars or 0
                for car, p in zip(self.cars, packet.data, strict=False):  # type: ignore[call-overload]
                    car.participant = p
            case PacketId.CAR_TELEMETRY:
                for car, t in zip(self.cars, packet.data, strict=False):  # type: ignore[call-overload]
                    car.telemetry = t
            case PacketId.CAR_STATUS:
                for car, s in zip(self.cars, packet.data, strict=False):  # type: ignore[call-overload]
                    car.status = s
            case PacketId.CAR_DAMAGE:
                for car, d in zip(self.cars, packet.data, strict=False):  # type: ignore[call-overload]
                    car.damage = d
            case PacketId.CAR_TELEMETRY_2:
                for car, t2 in zip(self.cars, packet.data, strict=False):  # type: ignore[call-overload]
                    car.telemetry2 = t2
            case PacketId.MOTION:
                for car, m in zip(self.cars, packet.data, strict=False):  # type: ignore[call-overload]
                    car.motion = m
            case PacketId.EVENT:
                self._on_event(packet.data)  # type: ignore[arg-type]

    def _reset(self, uid: int, fmt: int) -> None:
        log.info("new session %x (format %d)", uid, fmt)
        self.session_uid = uid
        self.packet_format = fmt
        self.session = None
        self.session_time = 0.0
        self.cars = []
        self.events = []
        self.timeline += 1
        self.session_started = False

    def _ensure_cars(self, packet: Packet) -> None:
        data = packet.data
        if isinstance(data, list) and len(self.cars) != len(data):
            self.cars = [CarState(i) for i in range(len(data))]

    @staticmethod
    def _update_lap(car: CarState, lap: LapData) -> None:
        prev = car.lap
        car.lap = lap
        # A new last-lap time means a lap just finished (this also catches the chequered flag, where
        # the lap number doesn't increment).
        finished = prev is not None and lap.last_lap_ms and lap.last_lap_ms != prev.last_lap_ms
        if finished and not prev.lap_invalid and (car.best_lap_ms is None or lap.last_lap_ms < car.best_lap_ms):
            car.best_lap_ms = lap.last_lap_ms

    def _on_event(self, event: Event) -> None:
        if event.code == "BUTN":
            return  # spams while any button is held; the wheel is read directly instead
        if event.code == "SSTA":
            self.session_started = True
        elif event.code == "FLBK":
            self.timeline += 1
        self.events.append(event)
