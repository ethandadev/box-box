"""Build World state directly for detector tests, without packing bytes."""

from __future__ import annotations

from dataclasses import replace

from box_box.config import Settings
from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Phrasebook, RadioCall
from box_box.state.model import World
from box_box.telemetry.packets import (
    CarDamage,
    CarStatus,
    CarTelemetry,
    CarTelemetry2,
    Event,
    Header,
    LapData,
    MarshalZone,
    Packet,
    PacketId,
    Participant,
    Session,
    WeatherSample,
)

UID = 0xC0FFEE

LAP = LapData(
    last_lap_ms=0,
    current_lap_ms=0,
    sector1_ms=0,
    sector2_ms=0,
    delta_to_car_in_front_ms=0,
    delta_to_leader_ms=0,
    lap_distance=0.0,
    total_distance=0.0,
    safety_car_delta=0.0,
    position=1,
    lap_num=1,
    pit_status=0,
    num_pit_stops=0,
    sector=0,
    lap_invalid=False,
    penalties_s=0,
    total_warnings=0,
    corner_cutting_warnings=0,
    unserved_drive_throughs=0,
    unserved_stop_gos=0,
    grid_position=1,
    driver_status=4,
    result_status=2,
    pit_lane_timer_active=False,
    pit_lane_time_ms=0,
    pit_stop_timer_ms=0,
    pit_stop_should_serve_pen=False,
    speed_trap_kph=0.0,
    speed_trap_lap=255,
)
STATUS = CarStatus(
    traction_control=0,
    abs=False,
    fuel_mix=1,
    front_brake_bias=56,
    pit_limiter=False,
    fuel_in_tank=30.0,
    fuel_capacity=110.0,
    fuel_remaining_laps=0.5,
    max_rpm=12000,
    idle_rpm=4000,
    max_gears=9,
    drs_allowed=False,
    drs_activation_distance=0,
    actual_compound=18,
    visual_compound=17,
    tyre_age_laps=3,
    fia_flag=0,
    ice_power_w=0.0,
    mguk_power_w=0.0,
    ers_store_j=3.0e6,
    ers_deploy_mode=1,
    ers_harvested_mguk_j=0.0,
    ers_harvested_mguh_j=0.0,
    ers_harvest_limit_j=6.0e6,
    ers_deployed_j=0.0,
    network_paused=False,
)
TELEMETRY = CarTelemetry(
    speed_kph=250,
    throttle=1.0,
    steer=0.0,
    brake=0.0,
    clutch=0,
    gear=7,
    rpm=11000,
    drs=False,
    brake_temps=(500, 500, 600, 600),
    tyre_surface_temps=(95, 95, 92, 92),
    tyre_inner_temps=(100, 100, 98, 98),
    engine_temp=110,
    tyre_pressures=(23.0, 23.0, 22.5, 22.5),
    surface_types=(0, 0, 0, 0),
)
DAMAGE = CarDamage(
    tyre_wear=(10.0, 10.0, 10.0, 10.0),
    tyre_damage=(0, 0, 0, 0),
    brake_damage=(0, 0, 0, 0),
    tyre_blisters=(0, 0, 0, 0),
    front_left_wing=0,
    front_right_wing=0,
    rear_wing=0,
    floor=0,
    diffuser=0,
    sidepod=0,
    drs_fault=False,
    ers_fault=False,
    gearbox=0,
    engine=0,
    engine_blown=False,
    engine_seized=False,
)
T2 = CarTelemetry2(False, False, 0, False, False, 0, True, False)
SESSION = Session(
    weather=0,
    track_temp=30,
    air_temp=22,
    total_laps=20,
    track_length=5000,
    session_type=15,
    track_id=7,
    formula=0,
    time_left_s=3600,
    duration_s=3600,
    pit_speed_limit=80,
    game_paused=False,
    is_spectating=False,
    marshal_zones=tuple(MarshalZone(i / 10, 0) for i in range(10)),
    safety_car_status=0,
    network_game=False,
    forecast=(),
    ai_difficulty=90,
    pit_window_ideal_lap=0,
    pit_window_latest_lap=0,
    pit_rejoin_position=0,
    sector2_start=1700.0,
    sector3_start=3400.0,
)

NAMES = ["Ethan", "NORRIS", "LECLERC", "HAMILTON", "RUSSELL", "PIASTRI"]


def header(pid: PacketId, t: float, player: int = 0) -> Header:
    return Header(2026, 25, 1, 0, 1, pid, UID, t, 0, 0, player, 255)


class Sim:
    """A World plus the detectors under test; mutate cars with `set_*`, then `tick()`."""

    def __init__(self, detectors: list[Detector], *, cars: int = 6, session: Session | None = None, settings: Settings | None = None):
        self.world = World()
        self.detectors = detectors
        self.settings = settings or Settings()
        self.say = Phrasebook("calm", seed=7)
        self.t = 10.0
        self.n = cars
        self.laps = [replace(LAP, position=i + 1, grid_position=i + 1, delta_to_leader_ms=i * 1000) for i in range(cars)]
        self.status = [STATUS for _ in range(cars)]
        self.telemetry = [TELEMETRY for _ in range(cars)]
        self.damage = [DAMAGE for _ in range(cars)]
        self.t2 = [T2 for _ in range(cars)]
        self.session = session or SESSION
        self.world.apply(Packet(header(PacketId.SESSION, self.t), PacketId.SESSION, self.session))
        parts = [Participant(i != 0, i, 0, 0, False, i + 1, 0, NAMES[i % len(NAMES)], True) for i in range(cars)]
        self.world.apply(Packet(header(PacketId.PARTICIPANTS, self.t), PacketId.PARTICIPANTS, parts, num_active_cars=cars))
        self.calls: list[RadioCall] = []
        self._push()
        self.world.drain_changes()

    def lap(self, i: int, **kw) -> None:
        self.laps[i] = replace(self.laps[i], **kw)

    def car_status(self, i: int, **kw) -> None:
        self.status[i] = replace(self.status[i], **kw)

    def car_telemetry(self, i: int, **kw) -> None:
        self.telemetry[i] = replace(self.telemetry[i], **kw)

    def car_damage(self, i: int, **kw) -> None:
        self.damage[i] = replace(self.damage[i], **kw)

    def car_t2(self, i: int, **kw) -> None:
        self.t2[i] = replace(self.t2[i], **kw)

    def set_session(self, **kw) -> None:
        self.session = replace(self.session, **kw)

    def event(self, code: str, **kw) -> None:
        self.world.apply(Packet(header(PacketId.EVENT, self.t), PacketId.EVENT, Event(code, **kw)))

    def _push(self) -> None:
        w, t = self.world, self.t
        w.apply(Packet(header(PacketId.SESSION, t), PacketId.SESSION, self.session))
        w.apply(Packet(header(PacketId.CAR_STATUS, t), PacketId.CAR_STATUS, list(self.status)))
        w.apply(Packet(header(PacketId.CAR_TELEMETRY, t), PacketId.CAR_TELEMETRY, list(self.telemetry)))
        w.apply(Packet(header(PacketId.CAR_DAMAGE, t), PacketId.CAR_DAMAGE, list(self.damage)))
        w.apply(Packet(header(PacketId.CAR_TELEMETRY_2, t), PacketId.CAR_TELEMETRY_2, list(self.t2)))
        w.apply(Packet(header(PacketId.LAP_DATA, t), PacketId.LAP_DATA, list(self.laps)))

    def tick(self, dt: float = 0.1) -> list[RadioCall]:
        self.t += dt
        self._push()
        ctx = Context(self.world, self.world.drain_changes(), self.world.drain_events(), self.settings, self.say)
        out: list[RadioCall] = []
        for d in self.detectors:
            out += d.update(ctx)
        self.calls += out
        return out

    def run(self, seconds: float, dt: float = 0.1) -> list[RadioCall]:
        out: list[RadioCall] = []
        for _ in range(round(seconds / dt)):
            out += self.tick(dt)
        return out

    def complete_lap(self, i: int, lap_ms: int, s1: int = 30000, s2: int = 30000, **kw) -> None:
        """Car i crosses the line: set the finished lap's sectors, then roll over."""
        cur = self.laps[i]
        self.lap(i, sector1_ms=s1, sector2_ms=s2, sector=2)
        self.tick()
        self.lap(i, lap_num=cur.lap_num + 1, last_lap_ms=lap_ms, sector1_ms=0, sector2_ms=0, sector=0, lap_invalid=False, **kw)

    def texts(self) -> list[str]:
        return [c.text for c in self.calls]
