"""The engineer's pit-wall screens: one live picture of the session, built up from decoded packets.

Everything downstream (detectors, the conversation tools) reads from `World`; nothing else touches packets.
Besides the latest values, the world keeps what an engineer remembers: every lap of every car, pit stops,
and a list of `changes` (lap completed, pitted, retired...) that detectors consume each tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

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
    SessionHistory,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LapRecord:
    lap_num: int
    lap_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    valid: bool
    position: int
    visual_compound: int | None
    tyre_age: int | None
    tyre_wear: float | None  # average wear % at the end of the lap
    fuel_kg: float | None  # fuel in tank at the end of the lap
    fuel_used_kg: float | None
    pit_lap: bool  # entered or left the pits during this lap (not representative pace)


# ---- changes: things that happened since the last tick ------------------------------------------


@dataclass(frozen=True, slots=True)
class LapCompleted:
    car_index: int
    record: LapRecord


@dataclass(frozen=True, slots=True)
class PitEntered:
    car_index: int


@dataclass(frozen=True, slots=True)
class PitExited:
    car_index: int
    stationary_ms: int
    new_compound: int | None


@dataclass(frozen=True, slots=True)
class PositionChanged:
    car_index: int
    old: int
    new: int


@dataclass(frozen=True, slots=True)
class Retired:
    car_index: int
    result_status: int


@dataclass(frozen=True, slots=True)
class SessionChanged:
    """A new session started (or the app joined one already running)."""


@dataclass(frozen=True, slots=True)
class TimelineRewound:
    """Flashback or save reload: anything remembered from the future is now wrong."""


Change = LapCompleted | PitEntered | PitExited | PositionChanged | Retired | SessionChanged | TimelineRewound


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
    history: SessionHistory | None = None
    laps: list[LapRecord] = field(default_factory=list)
    best_lap_ms: int | None = None
    pit_stops: int = 0
    _lap_start_fuel: float | None = None
    _lap_had_pit: bool = False
    _pit_stationary_ms: int = 0
    _rewound: bool = False  # a flashback happened; the next lap packet re-baselines instead of completing a lap

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
    def in_pit_lane(self) -> bool:
        return self.lap is not None and self.lap.pit_status != 0

    @property
    def speed_kph(self) -> float:
        return float(self.telemetry.speed_kph) if self.telemetry else 0.0

    @property
    def driver_status(self) -> DriverStatus | None:
        if self.lap is None:
            return None
        try:
            return DriverStatus(self.lap.driver_status)
        except ValueError:
            return None

    @property
    def position(self) -> int:
        return self.lap.position if self.lap else 0

    @property
    def compound(self) -> int | None:
        return self.status.visual_compound if self.status else None

    @property
    def tyre_wear_avg(self) -> float | None:
        if self.damage is None:
            return None
        return sum(self.damage.tyre_wear) / 4

    @property
    def session_best_ms(self) -> int | None:
        """Best valid lap this session: the game's history when we have it, otherwise what we saw."""
        hist = self.history.best_lap_ms if self.history else None
        candidates = [x for x in (hist, self.best_lap_ms) if x]
        return min(candidates) if candidates else None

    def valid_laps(self, include_pit_laps: bool = False) -> list[LapRecord]:
        return [r for r in self.laps if r.valid and r.lap_ms > 0 and (include_pit_laps or not r.pit_lap)]


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
    events: list[Event] = field(default_factory=list)  # game events since the last drain
    changes: list[Change] = field(default_factory=list)  # derived changes since the last drain
    # Bumped whenever time jumps backwards (flashback, save reload), so detectors can drop their memory.
    timeline: int = 0
    _rewound_at: float = -1e9  # session time of the last flashback we handled
    session_started: bool = False
    lights_out: bool = False
    chequered_flag: bool = False
    session_ended: bool = False
    fastest_lap: tuple[int, float] | None = None  # (car index, seconds) from FTLP events
    plan_extended_from: int = 0  # ideal pit lap the strategist told the driver we'd go past
    coach_note: str | None = None  # latest "lost two tenths at Copse..." from the corner coach
    last_packet_time: float = 0.0  # monotonic, set by the engine

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

    @property
    def weekend(self) -> tuple[int, ...]:
        return self.session.weekend if self.session else ()

    @property
    def is_sprint(self) -> bool:
        """The sprint race on a sprint weekend: short, flat out, normally no stop."""
        return names.is_sprint(self.session_type, self.weekend)

    @property
    def session_name(self) -> str:
        return names.session_name(self.session_type, self.weekend)

    @property
    def is_time_trial(self) -> bool:
        return self.session_type == names.TIME_TRIAL

    @property
    def regulations_2026(self) -> bool:
        me = self.player
        return bool(me and me.telemetry2 and me.telemetry2.regulations_2026)

    @property
    def total_laps(self) -> int:
        return self.session.total_laps if self.session else 0

    @property
    def laps_remaining(self) -> int | None:
        me = self.player
        if not (self.is_race and me and me.lap and self.total_laps):
            return None
        if me.lap.result_status == ResultStatus.FINISHED:
            return 0  # past the flag the lap counter stays on the last lap
        return max(0, self.total_laps - me.lap.lap_num + 1)

    def others(self) -> list[CarState]:
        return [c for c in self.cars if c.index != self.player_index and c.lap is not None]

    def running(self) -> list[CarState]:
        return [
            c
            for c in self.cars
            if c.lap is not None
            and c.lap.position > 0
            and c.lap.result_status in (ResultStatus.ACTIVE, ResultStatus.FINISHED)  # not retired or DSQ
        ]

    def by_position(self) -> list[CarState]:
        return sorted(self.running(), key=lambda c: c.lap.position)  # type: ignore[union-attr]

    def car_at_position(self, position: int) -> CarState | None:
        for c in self.running():
            if c.lap and c.lap.position == position:
                return c
        return None

    def find_driver(self, name: str) -> CarState | None:
        """Match a spoken driver name ("Leclerc", "the Ferrari", "car 16") to a car."""
        q = name.strip().lower()
        if not q:
            return None
        for c in self.running():
            if c.name.lower() == q or (c.participant and c.participant.name.lower() == q):
                return c
        for c in self.running():
            if q in c.name.lower() or c.name.lower() in q:
                return c
        if q.startswith("car ") and q[4:].isdigit():
            num = int(q[4:])
            for c in self.running():
                if c.participant and c.participant.race_number == num:
                    return c
        return None

    def race_gap_s(self, a: CarState, b: CarState) -> float | None:
        """Race gap in seconds from car a back to car b (positive = b is behind a), from the game's timing."""
        if not (a.lap and b.lap):
            return None
        da, db = a.lap.delta_to_leader_ms, b.lap.delta_to_leader_ms
        # The leader's own delta is 0; lapped cars read relative to the leader's lap and still subtract fine.
        if a.lap.position == 1:
            da = 0
        if b.lap.position == 1:
            db = 0
        return (db - da) / 1000.0

    def best_lap_overall(self) -> tuple[CarState, int] | None:
        best: tuple[CarState, int] | None = None
        for c in self.running():
            ms = c.session_best_ms
            if ms and (best is None or ms < best[1]):
                best = (c, ms)
        return best

    def best_sectors_overall(self) -> tuple[int | None, int | None, int | None]:
        b: list[int | None] = [None, None, None]
        for c in self.running():
            for r in c.valid_laps(include_pit_laps=True):
                for i, v in enumerate((r.sector1_ms, r.sector2_ms, r.sector3_ms)):
                    if v and (b[i] is None or v < b[i]):  # type: ignore[operator]
                        b[i] = v
            if c.history:
                for r in c.history.laps:
                    if not r.valid or not r.lap_ms:
                        continue
                    for i, v in enumerate((r.sector1_ms, r.sector2_ms, r.sector3_ms)):
                        if v and (b[i] is None or v < b[i]):  # type: ignore[operator]
                            b[i] = v
        return b[0], b[1], b[2]

    def drain_events(self) -> list[Event]:
        out, self.events = self.events, []
        return out

    def drain_changes(self) -> list[Change]:
        out, self.changes = self.changes, []
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
            self._rewind()
            self._rewound_at = h.session_time
        self.session_time = h.session_time
        self.player_index = h.player_car_index
        self._ensure_cars(packet)

        match packet.id:
            case PacketId.SESSION:
                new: Session = packet.data  # type: ignore[assignment]
                if not new.weekend and self.session is not None and self.session.weekend:
                    new = replace(new, weekend=self.session.weekend)  # some packets send it empty: keep it
                self.session = new
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
            case PacketId.SESSION_HISTORY:
                hist: SessionHistory = packet.data  # type: ignore[assignment]
                if 0 <= hist.car_index < len(self.cars):
                    self.cars[hist.car_index].history = hist
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
        self.changes = [SessionChanged()]
        self.timeline += 1
        self.session_started = False
        self.lights_out = False
        self.chequered_flag = False
        self.session_ended = False
        self.fastest_lap = None
        self.plan_extended_from = 0
        self.coach_note = None

    def _rewind(self) -> None:
        """The clock went backwards: forget laps from the abandoned future."""
        self.timeline += 1
        self.changes.append(TimelineRewound())
        for car in self.cars:
            if car.lap is not None:
                car.laps = [r for r in car.laps if r.lap_num < car.lap.lap_num]
                car._rewound = True

    def _ensure_cars(self, packet: Packet) -> None:
        data = packet.data
        if isinstance(data, list) and len(self.cars) != len(data):
            self.cars = [CarState(i) for i in range(len(data))]

    def _update_lap(self, car: CarState, lap: LapData) -> None:
        prev = car.lap
        car.lap = lap
        if prev is None:
            car._lap_start_fuel = car.status.fuel_in_tank if car.status else None
            return
        if car._rewound:
            # Straight after a flashback the lap number can step back and "last lap" changes to an older
            # lap's time. That isn't a lap being completed: drop the abandoned laps and carry on from here.
            car._rewound = False
            car.laps = [r for r in car.laps if r.lap_num < lap.lap_num]
            valid = [r.lap_ms for r in car.laps if r.valid and r.lap_ms > 0]
            car.best_lap_ms = min(valid) if valid else None
            car._lap_had_pit = lap.pit_status != 0
            car.pit_stops = lap.num_pit_stops  # a flashback to before a stop undoes it
            car._pit_stationary_ms = 0
            car._lap_start_fuel = None  # mid-lap now: this lap's fuel use can't be measured
            return

        # Pit lane transitions.
        if lap.pit_status != 0:
            car._lap_had_pit = True
            car._pit_stationary_ms = max(car._pit_stationary_ms, lap.pit_stop_timer_ms)
        if prev.pit_status == 0 and lap.pit_status != 0:
            self.changes.append(PitEntered(car.index))
        elif prev.pit_status != 0 and lap.pit_status == 0:
            if lap.num_pit_stops > prev.num_pit_stops or car._pit_stationary_ms > 0:
                car.pit_stops = max(car.pit_stops + 1, lap.num_pit_stops)
            self.changes.append(PitExited(car.index, car._pit_stationary_ms, car.compound))
            car._pit_stationary_ms = 0
        # The game's own count covers stops made before we joined the session (synced only on track,
        # so a count bumped while still in the box isn't added twice on pit exit).
        if lap.pit_status == 0:
            car.pit_stops = max(car.pit_stops, lap.num_pit_stops)

        # Lap completion: the lap number moves on, or the last-lap time changes (the chequered flag
        # updates the time without incrementing the lap).
        new_lap = lap.lap_num > prev.lap_num
        new_time = lap.last_lap_ms and lap.last_lap_ms != prev.last_lap_ms
        if (new_lap or new_time) and lap.last_lap_ms:
            s1, s2 = prev.sector1_ms, prev.sector2_ms
            s3 = lap.last_lap_ms - s1 - s2 if s1 and s2 else 0
            fuel = car.status.fuel_in_tank if car.status else None
            used = (car._lap_start_fuel - fuel) if (fuel is not None and car._lap_start_fuel is not None) else None
            record = LapRecord(
                lap_num=prev.lap_num,
                lap_ms=lap.last_lap_ms,
                sector1_ms=s1,
                sector2_ms=s2,
                sector3_ms=max(0, s3),
                valid=not prev.lap_invalid,
                position=lap.position,
                visual_compound=car.compound,
                tyre_age=car.status.tyre_age_laps if car.status else None,
                tyre_wear=car.tyre_wear_avg,
                fuel_kg=fuel,
                fuel_used_kg=used if used is not None and used >= 0 else None,
                pit_lap=car._lap_had_pit or prev.driver_status in (DriverStatus.OUT_LAP, DriverStatus.IN_LAP),
            )
            if not car.laps or car.laps[-1].lap_num != record.lap_num or car.laps[-1].lap_ms != record.lap_ms:
                car.laps.append(record)
                if record.valid and (car.best_lap_ms is None or record.lap_ms < car.best_lap_ms):
                    car.best_lap_ms = record.lap_ms
                self.changes.append(LapCompleted(car.index, record))
            car._lap_start_fuel = fuel
            car._lap_had_pit = lap.pit_status != 0

        if prev.position and lap.position and prev.position != lap.position and lap.result_status == 2:
            self.changes.append(PositionChanged(car.index, prev.position, lap.position))
        if prev.result_status == ResultStatus.ACTIVE and lap.result_status in (
            ResultStatus.DNF,
            ResultStatus.DSQ,
            ResultStatus.RETIRED,
            ResultStatus.NOT_CLASSIFIED,
        ):
            self.changes.append(Retired(car.index, lap.result_status))

    def _on_event(self, event: Event) -> None:
        if event.code == "BUTN":
            return  # spams while any button is held; handled by the push-to-talk service directly
        match event.code:
            case "SSTA":
                self.session_started = True
            case "SEND":
                self.session_ended = True
            case "LGOT":
                self.lights_out = True
            case "CHQF":
                self.chequered_flag = True
            case "FTLP":
                if event.vehicle_idx is not None and event.value:
                    self.fastest_lap = (event.vehicle_idx, float(event.value))
            case "FLBK":
                # The clock jumping back usually caught it already; don't rewind the same flashback twice.
                if abs(self.session_time - self._rewound_at) > 1.0:
                    self._rewind()
                    self._rewound_at = self.session_time
        self.events.append(event)
