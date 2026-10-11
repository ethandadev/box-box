"""Decoders for the F1 25 UDP telemetry, formats 2025 (base game) and 2026 (2026 Season Pack).

Unlike a dashboard, the engineer needs every car on track (traffic, gaps, who's on a push lap), so
per-car packets are decoded for all slots, not just the player's.

Source of truth: EA's "F1 25: 2026 Season Pack UDP Specification". Layouts were cross-checked against
MacManley/f1-26-udp and rdgonzaga/f1-game-telemetry (both MIT), and every packet size below is asserted
against the struct definitions at import time, so a typo in a format string fails loudly.

Everything is little-endian and packed. Wheel arrays are ordered RL, RR, FL, FR.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from enum import IntEnum

log = logging.getLogger(__name__)

HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 bytes, identical in both formats


class PacketId(IntEnum):
    MOTION = 0
    SESSION = 1
    LAP_DATA = 2
    EVENT = 3
    PARTICIPANTS = 4
    CAR_SETUPS = 5
    CAR_TELEMETRY = 6
    CAR_STATUS = 7
    FINAL_CLASSIFICATION = 8
    LOBBY_INFO = 9
    CAR_DAMAGE = 10
    SESSION_HISTORY = 11
    TYRE_SETS = 12
    MOTION_EX = 13
    TIME_TRIAL = 14
    LAP_POSITIONS = 15
    CAR_TELEMETRY_2 = 16


@dataclass(frozen=True, slots=True)
class Header:
    packet_format: int
    game_year: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int


# --------------------------------------------------------------------------------------------------
# Per-format layouts
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    max_cars: int
    packet_sizes: dict[int, int]
    motion: struct.Struct
    lap_data: struct.Struct
    participant: struct.Struct
    car_telemetry: struct.Struct
    car_status: struct.Struct
    car_damage: struct.Struct
    car_telemetry_2: struct.Struct | None


_LAP_DATA = struct.Struct("<IIHBHBHBHBfff15BHHBfB")  # 57, same in both formats
_CAR_DAMAGE = struct.Struct("<4f4B4B4B18B")  # 46, same in both formats

LAYOUTS: dict[int, Layout] = {
    2025: Layout(
        max_cars=22,
        packet_sizes={
            PacketId.MOTION: 1349,
            PacketId.SESSION: 753,
            PacketId.LAP_DATA: 1285,
            PacketId.EVENT: 45,
            PacketId.PARTICIPANTS: 1284,
            PacketId.CAR_SETUPS: 1133,
            PacketId.CAR_TELEMETRY: 1352,
            PacketId.CAR_STATUS: 1239,
            PacketId.FINAL_CLASSIFICATION: 1042,
            PacketId.LOBBY_INFO: 954,
            PacketId.CAR_DAMAGE: 1041,
            PacketId.SESSION_HISTORY: 1460,
            PacketId.TYRE_SETS: 231,
            PacketId.MOTION_EX: 273,
            PacketId.TIME_TRIAL: 101,
            PacketId.LAP_POSITIONS: 1131,
        },
        motion=struct.Struct("<6f6h3f3f"),  # 60: g-forces are floats
        lap_data=_LAP_DATA,
        participant=struct.Struct("<7B32sBBHBB12B"),  # 57: driver/network/team ids are u8
        car_telemetry=struct.Struct("<HfffBbHBBH4H4B4BH4f4B"),  # 60: engine temp u16
        car_status=struct.Struct("<5BfffHHBBHBBBbfffBfffB"),  # 55: no harvest limit
        car_damage=_CAR_DAMAGE,
        car_telemetry_2=None,
    ),
    2026: Layout(
        max_cars=24,
        packet_sizes={
            PacketId.MOTION: 1325,
            PacketId.SESSION: 926,
            PacketId.LAP_DATA: 1399,
            PacketId.EVENT: 45,
            PacketId.PARTICIPANTS: 1470,
            PacketId.CAR_SETUPS: 1233,
            PacketId.CAR_TELEMETRY: 1448,
            PacketId.CAR_STATUS: 1445,
            PacketId.FINAL_CLASSIFICATION: 1134,
            PacketId.LOBBY_INFO: 1062,
            PacketId.CAR_DAMAGE: 1133,
            PacketId.SESSION_HISTORY: 1460,
            PacketId.TYRE_SETS: 231,
            PacketId.MOTION_EX: 273,
            PacketId.TIME_TRIAL: 104,
            PacketId.LAP_POSITIONS: 1231,
            PacketId.CAR_TELEMETRY_2: 269,
        },
        motion=struct.Struct("<6f6h3h3f"),  # 54: g-forces quantised to int16 (/1000)
        lap_data=_LAP_DATA,
        participant=struct.Struct("<BHHHBBB32sBBHBB12B"),  # 60: driver/network/team ids are u16
        car_telemetry=struct.Struct("<HfffBbHBBH4H4B4BB4f4B"),  # 59: engine temp u8
        car_status=struct.Struct("<5BfffHHBBHBBBbfffBffffB"),  # 59: + ersHarvestLimitPerLap
        car_damage=_CAR_DAMAGE,
        car_telemetry_2=struct.Struct("<BBHBBHBB"),  # 10
    ),
}

_TRAILERS = {PacketId.LAP_DATA: 2, PacketId.CAR_TELEMETRY: 3, PacketId.PARTICIPANTS: 0}


def _check_layouts() -> None:
    """Fail at import if a format string disagrees with the spec's packet sizes."""
    h = HEADER.size
    for fmt, lay in LAYOUTS.items():
        n = lay.max_cars
        expected = {
            PacketId.MOTION: h + n * lay.motion.size,
            PacketId.LAP_DATA: h + n * lay.lap_data.size + 2,
            PacketId.PARTICIPANTS: h + 1 + n * lay.participant.size,
            PacketId.CAR_TELEMETRY: h + n * lay.car_telemetry.size + 3,
            PacketId.CAR_STATUS: h + n * lay.car_status.size,
            PacketId.CAR_DAMAGE: h + n * lay.car_damage.size,
        }
        if lay.car_telemetry_2 is not None:
            expected[PacketId.CAR_TELEMETRY_2] = h + n * lay.car_telemetry_2.size
        for pid, size in expected.items():
            if lay.packet_sizes[pid] != size:
                raise AssertionError(f"format {fmt} {pid.name}: layout gives {size}, spec says {lay.packet_sizes[pid]}")


_check_layouts()


# --------------------------------------------------------------------------------------------------
# Decoded data
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CarMotion:
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    g_lat: float
    g_lon: float
    g_vert: float
    yaw: float
    pitch: float
    roll: float


class DriverStatus(IntEnum):
    IN_GARAGE = 0
    FLYING_LAP = 1
    IN_LAP = 2
    OUT_LAP = 3
    ON_TRACK = 4


class ResultStatus(IntEnum):
    INVALID = 0
    INACTIVE = 1
    ACTIVE = 2
    FINISHED = 3
    DNF = 4
    DSQ = 5
    NOT_CLASSIFIED = 6
    RETIRED = 7


@dataclass(frozen=True, slots=True)
class LapData:
    last_lap_ms: int
    current_lap_ms: int
    sector1_ms: int
    sector2_ms: int
    delta_to_car_in_front_ms: int
    delta_to_leader_ms: int
    lap_distance: float
    total_distance: float
    safety_car_delta: float
    position: int
    lap_num: int
    pit_status: int  # 0 none, 1 pitting (lane), 2 in pit area (box)
    num_pit_stops: int
    sector: int  # 0-based
    lap_invalid: bool
    penalties_s: int
    total_warnings: int
    corner_cutting_warnings: int
    unserved_drive_throughs: int
    unserved_stop_gos: int
    grid_position: int
    driver_status: int
    result_status: int
    pit_lane_timer_active: bool
    pit_lane_time_ms: int
    pit_stop_timer_ms: int
    pit_stop_should_serve_pen: bool
    speed_trap_kph: float
    speed_trap_lap: int


@dataclass(frozen=True, slots=True)
class Participant:
    ai_controlled: bool
    driver_id: int
    network_id: int
    team_id: int
    my_team: bool
    race_number: int
    nationality: int
    name: str
    telemetry_public: bool


@dataclass(frozen=True, slots=True)
class CarTelemetry:
    speed_kph: int
    throttle: float
    steer: float
    brake: float
    clutch: int
    gear: int
    rpm: int
    drs: bool
    brake_temps: tuple[int, int, int, int]
    tyre_surface_temps: tuple[int, int, int, int]
    tyre_inner_temps: tuple[int, int, int, int]
    engine_temp: int
    tyre_pressures: tuple[float, float, float, float]
    surface_types: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class CarStatus:
    traction_control: int
    abs: bool
    fuel_mix: int
    front_brake_bias: int
    pit_limiter: bool
    fuel_in_tank: float
    fuel_capacity: float
    fuel_remaining_laps: float
    max_rpm: int
    idle_rpm: int
    max_gears: int
    drs_allowed: bool
    drs_activation_distance: int
    actual_compound: int
    visual_compound: int
    tyre_age_laps: int
    fia_flag: int  # -1 unknown, 0 none, 1 green, 2 blue, 3 yellow, 4 red
    ice_power_w: float
    mguk_power_w: float
    ers_store_j: float
    ers_deploy_mode: int
    ers_harvested_mguk_j: float
    ers_harvested_mguh_j: float
    ers_harvest_limit_j: float | None  # 2026 only
    ers_deployed_j: float
    network_paused: bool


@dataclass(frozen=True, slots=True)
class CarDamage:
    tyre_wear: tuple[float, float, float, float]
    tyre_damage: tuple[int, int, int, int]
    brake_damage: tuple[int, int, int, int]
    tyre_blisters: tuple[int, int, int, int]
    front_left_wing: int
    front_right_wing: int
    rear_wing: int
    floor: int
    diffuser: int
    sidepod: int
    drs_fault: bool
    ers_fault: bool
    gearbox: int
    engine: int
    engine_blown: bool
    engine_seized: bool


@dataclass(frozen=True, slots=True)
class CarTelemetry2:
    active_aero_straight_mode: bool
    active_aero_available: bool
    active_aero_activation_distance: int
    overtake_available: bool
    overtake_active: bool  # armed, not necessarily deploying
    overtake_activation_distance: int
    regulations_2026: bool
    driving_wrong_way: bool


@dataclass(frozen=True, slots=True)
class MarshalZone:
    start: float  # lap fraction
    flag: int


@dataclass(frozen=True, slots=True)
class WeatherSample:
    session_type: int
    time_offset_min: int
    weather: int
    track_temp: int
    air_temp: int
    rain_percentage: int


@dataclass(frozen=True, slots=True)
class Session:
    weather: int
    track_temp: int
    air_temp: int
    total_laps: int
    track_length: int
    session_type: int
    track_id: int
    formula: int
    time_left_s: int
    duration_s: int
    pit_speed_limit: int
    game_paused: bool
    is_spectating: bool
    marshal_zones: tuple[MarshalZone, ...]
    safety_car_status: int  # 0 none, 1 full, 2 virtual, 3 formation
    network_game: bool
    forecast: tuple[WeatherSample, ...]
    ai_difficulty: int
    pit_window_ideal_lap: int
    pit_window_latest_lap: int
    pit_rejoin_position: int
    sector2_start: float
    sector3_start: float
    # 2026 only (empty in 2025). Zones are (start, end) lap fractions and may wrap the line.
    active_aero_zones: tuple[tuple[float, float], ...] = ()
    drs_zones: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class Event:
    code: str
    # Decoded details for the codes the engineer cares about; raw bytes always available.
    vehicle_idx: int | None = None
    other_vehicle_idx: int | None = None
    value: float | None = None
    detail: dict[str, int | float] = field(default_factory=dict)
    raw: bytes = b""


@dataclass(frozen=True, slots=True)
class HistoryLap:
    lap_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    valid: bool  # whole lap valid (bit 0 of the lap's valid flags)


@dataclass(frozen=True, slots=True)
class SessionHistory:
    """One car's lap history. The game sends one car per packet, cycling through the field."""

    car_index: int
    laps: tuple[HistoryLap, ...]  # completed and in-progress laps, oldest first
    best_lap_num: int  # 1-based, 0 = none
    best_sector_laps: tuple[int, int, int]
    stints: tuple[tuple[int, int, int], ...]  # (end lap, actual compound, visual compound); end 255 = current

    @property
    def best_lap_ms(self) -> int | None:
        if 1 <= self.best_lap_num <= len(self.laps):
            ms = self.laps[self.best_lap_num - 1].lap_ms
            return ms or None
        return None


@dataclass(frozen=True, slots=True)
class Packet:
    header: Header
    id: PacketId
    # One of: list[CarMotion], Session, list[LapData], Event, list[Participant], list[CarTelemetry],
    # list[CarStatus], list[CarDamage], list[CarTelemetry2], or None for recognised-but-unparsed packets.
    data: object
    num_active_cars: int | None = None


# --------------------------------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------------------------------


def _slots(s: struct.Struct, data: bytes, offset: int, count: int):
    return struct.iter_unpack(s.format, data[offset : offset + s.size * count])


def _motion(lay: Layout, data: bytes, fmt: int) -> list[CarMotion]:
    g_scale = 1000.0 if fmt >= 2026 else 1.0
    out = []
    for v in _slots(lay.motion, data, HEADER.size, lay.max_cars):
        out.append(
            CarMotion(
                v[0], v[1], v[2], v[3], v[4], v[5],
                v[12] / g_scale, v[13] / g_scale, v[14] / g_scale,
                v[15], v[16], v[17],
            )
        )  # fmt: skip
    return out


def _lap_data(lay: Layout, data: bytes) -> list[LapData]:
    out = []
    for v in _slots(lay.lap_data, data, HEADER.size, lay.max_cars):
        out.append(
            LapData(
                last_lap_ms=v[0],
                current_lap_ms=v[1],
                sector1_ms=v[3] * 60_000 + v[2],
                sector2_ms=v[5] * 60_000 + v[4],
                delta_to_car_in_front_ms=v[7] * 60_000 + v[6],
                delta_to_leader_ms=v[9] * 60_000 + v[8],
                lap_distance=v[10],
                total_distance=v[11],
                safety_car_delta=v[12],
                position=v[13],
                lap_num=v[14],
                pit_status=v[15],
                num_pit_stops=v[16],
                sector=v[17],
                lap_invalid=bool(v[18]),
                penalties_s=v[19],
                total_warnings=v[20],
                corner_cutting_warnings=v[21],
                unserved_drive_throughs=v[22],
                unserved_stop_gos=v[23],
                grid_position=v[24],
                driver_status=v[25],
                result_status=v[26],
                pit_lane_timer_active=bool(v[27]),
                pit_lane_time_ms=v[28],
                pit_stop_timer_ms=v[29],
                pit_stop_should_serve_pen=bool(v[30]),
                speed_trap_kph=v[31],
                speed_trap_lap=v[32],
            )
        )
    return out


def _decode_name(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def _participants(lay: Layout, data: bytes) -> tuple[int, list[Participant]]:
    num_active = data[HEADER.size]
    out = []
    for v in _slots(lay.participant, data, HEADER.size + 1, lay.max_cars):
        # Both layouts: ai, driverId, networkId, teamId, myTeam, raceNumber, nationality, name, yourTelemetry, ...
        out.append(
            Participant(
                ai_controlled=bool(v[0]),
                driver_id=v[1],
                network_id=v[2],
                team_id=v[3],
                my_team=bool(v[4]),
                race_number=v[5],
                nationality=v[6],
                name=_decode_name(v[7]),
                telemetry_public=bool(v[8]),
            )
        )
    return num_active, out


def _car_telemetry(lay: Layout, data: bytes) -> list[CarTelemetry]:
    out = []
    for v in _slots(lay.car_telemetry, data, HEADER.size, lay.max_cars):
        out.append(
            CarTelemetry(
                speed_kph=v[0],
                throttle=v[1],
                steer=v[2],
                brake=v[3],
                clutch=v[4],
                gear=v[5],
                rpm=v[6],
                drs=bool(v[7]),
                brake_temps=v[10:14],
                tyre_surface_temps=v[14:18],
                tyre_inner_temps=v[18:22],
                engine_temp=v[22],
                tyre_pressures=v[23:27],
                surface_types=v[27:31],
            )
        )
    return out


def _car_status(lay: Layout, data: bytes, fmt: int) -> list[CarStatus]:
    out = []
    has_limit = fmt >= 2026
    for v in _slots(lay.car_status, data, HEADER.size, lay.max_cars):
        if has_limit:
            limit, deployed, paused = v[23], v[24], v[25]
        else:
            limit, deployed, paused = None, v[23], v[24]
        out.append(
            CarStatus(
                traction_control=v[0],
                abs=bool(v[1]),
                fuel_mix=v[2],
                front_brake_bias=v[3],
                pit_limiter=bool(v[4]),
                fuel_in_tank=v[5],
                fuel_capacity=v[6],
                fuel_remaining_laps=v[7],
                max_rpm=v[8],
                idle_rpm=v[9],
                max_gears=v[10],
                drs_allowed=bool(v[11]),
                drs_activation_distance=v[12],
                actual_compound=v[13],
                visual_compound=v[14],
                tyre_age_laps=v[15],
                fia_flag=v[16],
                ice_power_w=v[17],
                mguk_power_w=v[18],
                ers_store_j=v[19],
                ers_deploy_mode=v[20],
                ers_harvested_mguk_j=v[21],
                ers_harvested_mguh_j=v[22],
                ers_harvest_limit_j=limit,
                ers_deployed_j=deployed,
                network_paused=bool(paused),
            )
        )
    return out


def _car_damage(lay: Layout, data: bytes) -> list[CarDamage]:
    out = []
    for v in _slots(lay.car_damage, data, HEADER.size, lay.max_cars):
        out.append(
            CarDamage(
                tyre_wear=v[0:4],
                tyre_damage=v[4:8],
                brake_damage=v[8:12],
                tyre_blisters=v[12:16],
                front_left_wing=v[16],
                front_right_wing=v[17],
                rear_wing=v[18],
                floor=v[19],
                diffuser=v[20],
                sidepod=v[21],
                drs_fault=bool(v[22]),
                ers_fault=bool(v[23]),
                gearbox=v[24],
                engine=v[25],
                # v[26:32] are per-component engine wear
                engine_blown=bool(v[32]),
                engine_seized=bool(v[33]),
            )
        )
    return out


def _car_telemetry_2(lay: Layout, data: bytes) -> list[CarTelemetry2]:
    assert lay.car_telemetry_2 is not None
    return [
        CarTelemetry2(bool(v[0]), bool(v[1]), v[2], bool(v[3]), bool(v[4]), v[5], bool(v[6]), bool(v[7]))
        for v in _slots(lay.car_telemetry_2, data, HEADER.size, lay.max_cars)
    ]


_SESSION_INFO = struct.Struct("<BbbBHBbBHHBBBBBB")  # weather .. numMarshalZones, at offset 29
_MARSHAL_ZONE = struct.Struct("<fb")
_FORECAST = struct.Struct("<BBBbbbbB")
_SESSION_MARSHAL_OFFSET = HEADER.size + _SESSION_INFO.size  # 48
_SESSION_SC_OFFSET = _SESSION_MARSHAL_OFFSET + 21 * _MARSHAL_ZONE.size  # 153
_SESSION_FORECAST_OFFSET = _SESSION_SC_OFFSET + 3  # 156
_SESSION_AFTER_FORECAST = _SESSION_FORECAST_OFFSET + 64 * _FORECAST.size  # 668
_SESSION_TAIL = struct.Struct("<BBIIIBBB")  # forecastAccuracy, aiDifficulty, 3 link ids, pit window x3
_SESSION_SECTORS = struct.Struct("<ff")
_SESSION_SECTORS_OFFSET = 753 - _SESSION_SECTORS.size  # last field of the 2025 packet
_ZONE = struct.Struct("<ff")


def _zones(data: bytes, count_offset: int, max_zones: int) -> tuple[tuple[tuple[float, float], ...], int]:
    count = min(data[count_offset], max_zones)
    start = count_offset + 1
    zones = tuple(_ZONE.unpack_from(data, start + i * _ZONE.size) for i in range(count))
    return zones, start + max_zones * _ZONE.size


def _session(data: bytes, fmt: int) -> Session:
    v = _SESSION_INFO.unpack_from(data, HEADER.size)
    num_zones = min(v[15], 21)
    zones = tuple(
        MarshalZone(*_MARSHAL_ZONE.unpack_from(data, _SESSION_MARSHAL_OFFSET + i * _MARSHAL_ZONE.size))
        for i in range(num_zones)
    )
    sc_status, network_game, num_forecast = data[_SESSION_SC_OFFSET : _SESSION_SC_OFFSET + 3]
    forecast = []
    for i in range(min(num_forecast, 64)):
        f = _FORECAST.unpack_from(data, _SESSION_FORECAST_OFFSET + i * _FORECAST.size)
        forecast.append(WeatherSample(f[0], f[1], f[2], f[3], f[5], f[7]))
    tail = _SESSION_TAIL.unpack_from(data, _SESSION_AFTER_FORECAST)
    s2, s3 = _SESSION_SECTORS.unpack_from(data, _SESSION_SECTORS_OFFSET)

    aero: tuple[tuple[float, float], ...] = ()
    drs: tuple[tuple[float, float], ...] = ()
    if fmt >= 2026:
        # activeAeroTrackStatus (0 full, 1 partial), then the full list, the partial list, then DRS zones.
        status_offset = 753
        aero_status = data[status_offset]
        full, after_full = _zones(data, status_offset + 1, 8)
        partial, after_partial = _zones(data, after_full, 8)
        drs, _ = _zones(data, after_partial, 4)
        aero = partial if aero_status == 1 else full

    return Session(
        weather=v[0],
        track_temp=v[1],
        air_temp=v[2],
        total_laps=v[3],
        track_length=v[4],
        session_type=v[5],
        track_id=v[6],
        formula=v[7],
        time_left_s=v[8],
        duration_s=v[9],
        pit_speed_limit=v[10],
        game_paused=bool(v[11]),
        is_spectating=bool(v[12]),
        marshal_zones=zones,
        safety_car_status=sc_status,
        network_game=bool(network_game),
        forecast=tuple(forecast),
        ai_difficulty=tail[1],
        pit_window_ideal_lap=tail[5],
        pit_window_latest_lap=tail[6],
        pit_rejoin_position=tail[7],
        sector2_start=s2,
        sector3_start=s3,
        active_aero_zones=aero,
        drs_zones=drs,
    )


_HISTORY_HEAD = struct.Struct("<7B")
_HISTORY_LAP = struct.Struct("<IHBHBHBB")  # 14 bytes
_HISTORY_STINT = struct.Struct("<3B")


def _session_history(data: bytes) -> SessionHistory:
    car, num_laps, num_stints, best, b1, b2, b3 = _HISTORY_HEAD.unpack_from(data, HEADER.size)
    base = HEADER.size + _HISTORY_HEAD.size
    laps = []
    for i in range(min(num_laps, 100)):
        v = _HISTORY_LAP.unpack_from(data, base + i * _HISTORY_LAP.size)
        laps.append(
            HistoryLap(
                lap_ms=v[0],
                sector1_ms=v[2] * 60_000 + v[1],
                sector2_ms=v[4] * 60_000 + v[3],
                sector3_ms=v[6] * 60_000 + v[5],
                valid=bool(v[7] & 0x01),
            )
        )
    stint_base = base + 100 * _HISTORY_LAP.size
    stints = tuple(_HISTORY_STINT.unpack_from(data, stint_base + i * 3) for i in range(min(num_stints, 8)))
    return SessionHistory(car, tuple(laps), best, (b1, b2, b3), stints)


_EVENT_DETAILS = HEADER.size + 4


def _event(data: bytes, fmt: int) -> Event:
    code = data[HEADER.size : HEADER.size + 4].decode("ascii", errors="replace")
    d = data[_EVENT_DETAILS:]
    u = struct.unpack_from
    match code:
        case "FTLP":
            idx, t = u("<Bf", d)
            return Event(code, vehicle_idx=idx, value=t, raw=d)
        case "RTMT":
            idx, reason = u("<BB", d)
            return Event(code, vehicle_idx=idx, detail={"reason": reason}, raw=d)
        case "DRSD":
            return Event(code, detail={"reason": d[0]}, raw=d)
        case "TMPT" | "RCWN" | "DTSV":
            return Event(code, vehicle_idx=d[0], raw=d)
        case "PENA":
            ptype, infr, idx, other, t, lap, places = u("<7B", d)
            return Event(
                code,
                vehicle_idx=idx,
                other_vehicle_idx=other,
                value=t,
                detail={
                    "penalty_type": ptype,
                    "infringement_type": infr,
                    "lap": lap,
                    "places_gained": places,
                },
                raw=d,
            )
        case "SPTP":
            idx, speed, overall, driver, fastest_idx, fastest_speed = u("<BfBBBf", d)
            return Event(
                code,
                vehicle_idx=idx,
                value=speed,
                detail={"overall_fastest": overall, "driver_fastest": driver, "fastest_speed": fastest_speed},
                raw=d,
            )
        case "STLG":
            return Event(code, value=d[0], raw=d)
        case "SGSV":
            idx, t = u("<Bf", d)
            return Event(code, vehicle_idx=idx, value=t, raw=d)
        case "FLBK":
            frame, t = u("<If", d)
            return Event(code, value=t, detail={"frame": frame}, raw=d)
        case "BUTN":
            (buttons,) = u("<I", d)
            return Event(code, detail={"buttons": buttons}, raw=d)
        case "OVTK":
            return Event(code, vehicle_idx=d[0], other_vehicle_idx=d[1], raw=d)
        case "SCAR":
            return Event(code, detail={"safety_car_type": d[0], "event_type": d[1]}, raw=d)
        case "COLL":
            detail = {"severity": d[2]} if fmt >= 2026 else {}
            return Event(code, vehicle_idx=d[0], other_vehicle_idx=d[1], detail=detail, raw=d)
        case _:
            return Event(code, raw=d)


class Decoder:
    """Turns raw datagrams into `Packet`s. Unknown formats and wrong sizes are dropped with one warning each."""

    # What the engineer actually reads. Motion, setups, tyre sets etc. are skipped unless asked for:
    # at 60 Hz, decoding all 24 cars of every packet type is most of the CPU this app uses.
    DEFAULT_WANTED = frozenset(
        {
            PacketId.SESSION,
            PacketId.LAP_DATA,
            PacketId.EVENT,
            PacketId.PARTICIPANTS,
            PacketId.CAR_TELEMETRY,
            PacketId.CAR_STATUS,
            PacketId.CAR_DAMAGE,
            PacketId.SESSION_HISTORY,
            PacketId.CAR_TELEMETRY_2,
        }
    )

    def __init__(self, wanted: frozenset[int] | set[int] | None = None) -> None:
        self.wanted = frozenset(PacketId) if wanted is None else frozenset(wanted)
        self._warned: set[tuple[object, ...]] = set()
        self.last_warning: str | None = None
        self.packets = 0  # valid packets seen (wanted or not), for the diagnostics page
        self.dropped = 0
        self.last_format: int | None = None

    def decode(self, data: bytes) -> Packet | None:
        if len(data) < HEADER.size:
            self.dropped += 1
            self._warn(("short",), f"dropping {len(data)}-byte datagram (shorter than a header)")
            return None
        fmt = data[0] | data[1] << 8
        lay = LAYOUTS.get(fmt)
        if lay is None:
            self.dropped += 1
            self._warn(("format", fmt), f"unsupported UDP format {fmt}: set UDP Format to 2026 (or 2025) in-game")
            return None
        pid = data[6]
        expected = lay.packet_sizes.get(pid)
        if expected is None:
            return None
        if len(data) != expected:
            self.dropped += 1
            self._warn(("size", fmt, pid), f"format {fmt} packet {pid}: expected {expected} bytes, got {len(data)}")
            return None
        self.packets += 1
        self.last_format = fmt
        if pid not in self.wanted:
            return None

        header = Header(*HEADER.unpack_from(data))
        pid = PacketId(pid)
        match pid:
            case PacketId.MOTION:
                return Packet(header, pid, _motion(lay, data, fmt))
            case PacketId.SESSION:
                return Packet(header, pid, _session(data, fmt))
            case PacketId.LAP_DATA:
                return Packet(header, pid, _lap_data(lay, data))
            case PacketId.EVENT:
                return Packet(header, pid, _event(data, fmt))
            case PacketId.PARTICIPANTS:
                num_active, parts = _participants(lay, data)
                return Packet(header, pid, parts, num_active_cars=num_active)
            case PacketId.CAR_TELEMETRY:
                return Packet(header, pid, _car_telemetry(lay, data))
            case PacketId.CAR_STATUS:
                return Packet(header, pid, _car_status(lay, data, fmt))
            case PacketId.CAR_DAMAGE:
                return Packet(header, pid, _car_damage(lay, data))
            case PacketId.CAR_TELEMETRY_2:
                return Packet(header, pid, _car_telemetry_2(lay, data))
            case PacketId.SESSION_HISTORY:
                return Packet(header, pid, _session_history(data))
            case _:
                return Packet(header, pid, None)

    def _warn(self, key: tuple[object, ...], message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)
            self.last_warning = message
