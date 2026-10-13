"""What the engineer can look up. Shared by the instant offline answers and the AI's tool calls.

Every function reads the live World and returns plain data. Numbers never come from the LLM's
imagination: it has to call one of these.
"""

from __future__ import annotations

import random
from typing import Any

from box_box.engineer import damage, pitplan
from box_box.engineer.radio import lap_time
from box_box.state.model import CarState, World
from box_box.telemetry import names
from box_box.telemetry.packets import ResultStatus

CORNERS = ("rear_left", "rear_right", "front_left", "front_right")
SLICKS = {16, 17, 18, 19, 20, 21, 22}


def _car_brief(world: World, car: CarState) -> dict[str, Any]:
    lap = car.lap
    return {
        "name": car.name,
        "team": car.team,
        "position": lap.position if lap else None,
        "tyres": names.compound_name(car.compound) if car.compound is not None else None,
        "tyre_age_laps": car.status.tyre_age_laps if car.status else None,
        "pit_stops": car.pit_stops or (lap.num_pit_stops if lap else 0),
        "best_lap": lap_time(car.session_best_ms) if car.session_best_ms else None,
        "last_lap": lap_time(lap.last_lap_ms) if lap and lap.last_lap_ms else None,
        "in_pits": car.in_pit_lane,
    }


def session_info(world: World) -> dict[str, Any]:
    s = world.session
    me = world.player
    if s is None or me is None or me.lap is None:
        return {"error": "No session data yet. Is the game running with UDP telemetry on?"}
    info: dict[str, Any] = {
        "track": names.track_name(s.track_id),
        "session": world.session_name,
        "weather": names.WEATHER.get(s.weather, "unknown"),
        "track_temp_c": s.track_temp,
        "air_temp_c": s.air_temp,
        "safety_car": {0: "none", 1: "safety car", 2: "virtual safety car", 3: "formation lap"}.get(
            s.safety_car_status, "none"
        ),
        "my_position": me.lap.position,
        "my_lap": me.lap.lap_num,
        "cars": len(world.running()),
    }
    if me.lap.result_status == ResultStatus.FINISHED:
        info["finished"] = True
    if world.is_race:
        info["total_laps"] = world.total_laps
        info["laps_remaining"] = world.laps_remaining
        if s.pit_window_ideal_lap:
            info["pit_window_ideal_lap"] = s.pit_window_ideal_lap
            info["pit_window_latest_lap"] = s.pit_window_latest_lap
    else:
        info["time_left_min"] = round(s.time_left_s / 60, 1)
    return info


def gaps(world: World) -> dict[str, Any]:
    me = world.player
    if me is None or me.lap is None:
        return {"error": "No timing data yet."}
    out: dict[str, Any] = {"my_position": me.lap.position}
    if world.is_race:
        ahead = world.car_at_position(me.lap.position - 1)
        behind = world.car_at_position(me.lap.position + 1)
        leader = world.car_at_position(1)
        if ahead:
            out["ahead"] = {"name": ahead.name, "gap_s": round(world.race_gap_s(ahead, me) or 0, 1)}
        if behind:
            out["behind"] = {"name": behind.name, "gap_s": round(world.race_gap_s(me, behind) or 0, 1)}
        if leader and leader is not me:
            out["to_leader_s"] = round(world.race_gap_s(leader, me) or 0, 1)
    else:
        mine = me.session_best_ms
        best = world.best_lap_overall()
        out["my_best"] = lap_time(mine) if mine else None
        if best and mine:
            out["fastest"] = {"name": best[0].name, "time": lap_time(best[1])}
            out["off_fastest_s"] = round((mine - best[1]) / 1000, 3)
    return out


def standings(world: World, top: int = 10) -> dict[str, Any]:
    cars = world.by_position()
    return {"standings": [_car_brief(world, c) for c in cars[: max(1, min(top, 24))]]}


def driver(world: World, name: str) -> dict[str, Any]:
    car = world.find_driver(name)
    if car is None:
        return {"error": f"No driver matching '{name}'."}
    info = _car_brief(world, car)
    me = world.player
    if world.is_race and me and me.lap and car.lap and car is not me:
        g = world.race_gap_s(car, me)
        if g is not None:
            info["gap_to_me_s"] = round(g, 1)
            info["relation"] = "ahead of you" if g > 0 else "behind you"
    return info


def tyres(world: World) -> dict[str, Any]:
    me = world.player
    if me is None or me.status is None:
        return {"error": "No tyre data yet."}
    out: dict[str, Any] = {
        "compound": names.compound_name(me.status.visual_compound),
        "age_laps": me.status.tyre_age_laps,
    }
    if me.damage:
        out["wear_pct"] = {c: round(w) for c, w in zip(CORNERS, me.damage.tyre_wear, strict=True)}
        out["average_wear_pct"] = round(sum(me.damage.tyre_wear) / 4)
        rate = wear_rate_per_lap(me)
        if rate:
            out["wear_per_lap_pct"] = round(rate, 1)
            out["laps_until_70pct"] = max(0, round((70 - out["average_wear_pct"]) / rate)) if rate > 0 else None
    if me.telemetry:
        out["carcass_temp_c"] = dict(zip(CORNERS, me.telemetry.tyre_inner_temps, strict=True))
        out["surface_temp_c"] = dict(zip(CORNERS, me.telemetry.tyre_surface_temps, strict=True))
        out["pressures_psi"] = {c: round(p, 1) for c, p in zip(CORNERS, me.telemetry.tyre_pressures, strict=True)}
    return out


def wear_rate_per_lap(me: CarState) -> float | None:
    stint = [r for r in me.laps if r.tyre_wear is not None and r.tyre_age is not None]
    if len(stint) < 2:
        return None
    # Only this stint: tyre age must be increasing back from the latest lap.
    cur = [stint[-1]]
    for r in reversed(stint[:-1]):
        if r.tyre_age is not None and cur[0].tyre_age is not None and r.tyre_age < cur[0].tyre_age:
            cur.insert(0, r)
        else:
            break
    if len(cur) < 2:
        return None
    laps = (cur[-1].tyre_age or 0) - (cur[0].tyre_age or 0)
    return ((cur[-1].tyre_wear or 0) - (cur[0].tyre_wear or 0)) / laps if laps > 0 else None


def car_health(world: World) -> dict[str, Any]:
    me = world.player
    if me is None or me.damage is None:
        return {"error": "No car data yet."}
    d = me.damage
    out: dict[str, Any] = {
        "front_wing_left_pct": d.front_left_wing,
        "front_wing_right_pct": d.front_right_wing,
        "rear_wing_pct": d.rear_wing,
        "floor_pct": d.floor,
        "diffuser_pct": d.diffuser,
        "sidepod_pct": d.sidepod,
        "gearbox_pct": d.gearbox,
        "engine_pct": d.engine,
        "drs_fault": d.drs_fault,
        "ers_fault": d.ers_fault,
        "tyre_damage_pct": dict(zip(CORNERS, d.tyre_damage, strict=True)),
        # Say this, not the percentages: how an engineer actually describes damage on the radio.
        "summary": damage.report(d, random.Random(), race=world.is_race),
    }
    if me.telemetry:
        out["brake_temp_c"] = dict(zip(CORNERS, me.telemetry.brake_temps, strict=True))
        out["engine_temp_c"] = me.telemetry.engine_temp
    return out


def fuel(world: World) -> dict[str, Any]:
    me = world.player
    if me is None or me.status is None:
        return {"error": "No fuel data yet."}
    s = me.status
    out: dict[str, Any] = {"fuel_kg": round(s.fuel_in_tank, 1)}
    if world.is_race:
        out["margin_laps_at_finish"] = round(s.fuel_remaining_laps, 2)
        out["verdict"] = "short, need to save" if s.fuel_remaining_laps < -0.2 else "fine"
    else:
        out["laps_of_fuel"] = round(s.fuel_remaining_laps, 1)
    used = [r.fuel_used_kg for r in me.laps[-5:] if r.fuel_used_kg]
    if used:
        out["kg_per_lap"] = round(sum(used) / len(used), 2)
    return out


def energy(world: World) -> dict[str, Any]:
    me = world.player
    if me is None or me.status is None:
        return {"error": "No energy data yet."}
    s = me.status
    out: dict[str, Any] = {
        "battery_pct": round(s.ers_store_j / 4e6 * 100),
        "deploy_mode": {
            0: "none",
            1: "medium",
            2: "hotlap",
            3: "boost" if world.packet_format >= 2026 else "overtake",
        }.get(s.ers_deploy_mode, "unknown"),
    }
    if me.telemetry2:
        t2 = me.telemetry2
        out["overtake_available"] = t2.overtake_available
        out["overtake_armed"] = t2.overtake_active
        out["active_aero"] = "straight mode" if t2.active_aero_straight_mode else "corner mode"
    return out


def weather(world: World) -> dict[str, Any]:
    s = world.session
    if s is None:
        return {"error": "No session data yet."}
    forecast = [
        {
            "in_min": f.time_offset_min,
            "weather": names.WEATHER.get(f.weather, "?"),
            "rain_pct": f.rain_percentage,
            "track_temp_c": f.track_temp,
        }
        for f in s.forecast
        if f.session_type == s.session_type
    ][:6]
    return {
        "now": names.WEATHER.get(s.weather, "?"),
        "track_temp_c": s.track_temp,
        "air_temp_c": s.air_temp,
        "forecast": forecast,
    }


def lap_times(world: World, name: str | None = None, last: int = 5) -> dict[str, Any]:
    car = world.find_driver(name) if name else world.player
    if car is None:
        return {"error": f"No driver matching '{name}'."}
    laps = [
        {
            "lap": r.lap_num,
            "time": lap_time(r.lap_ms),
            "sectors": [round(r.sector1_ms / 1000, 3), round(r.sector2_ms / 1000, 3), round(r.sector3_ms / 1000, 3)],
            "valid": r.valid,
            "out_or_in_lap": r.pit_lap,
        }
        for r in car.laps[-max(1, min(last, 20)) :]
    ]
    return {"driver": car.name, "best": lap_time(car.session_best_ms) if car.session_best_ms else None, "laps": laps}


def strategy(world: World) -> dict[str, Any]:
    me = world.player
    s = world.session
    if me is None or s is None or me.lap is None:
        return {"error": "No data yet."}
    out: dict[str, Any] = {"pit_stops_done": me.pit_stops}
    if s.pit_window_ideal_lap:
        out["game_pit_window"] = {"ideal_lap": s.pit_window_ideal_lap, "latest_lap": s.pit_window_latest_lap}
        out["predicted_rejoin_position"] = s.pit_rejoin_position or None
    me_car = world.player
    plan = pitplan.rejoin(world, me_car) if me_car is not None else None
    if plan is not None:
        out["if_we_box_now"] = {
            "rejoin_position": plan.position,
            "places_lost": plan.places_lost,
            "pit_loss_s": round(plan.pit_loss_s, 1),
            "behind_car": {"name": plan.ahead[0].name, "gap_s": plan.ahead[1]} if plan.ahead else None,
            "ahead_of_car": {"name": plan.behind[0].name, "gap_s": plan.behind[1]} if plan.behind else None,
        }
        tyre = pitplan.next_tyre(world, me_car) if me_car is not None else None
        if tyre is not None:
            out["if_we_box_now"]["tyre"] = ("fresh " if tyre.fresh else "used ") + tyre.compound
    t = tyres(world)
    if "laps_until_70pct" in t and t["laps_until_70pct"] is not None:
        out["tyre_life_laps_left_estimate"] = t["laps_until_70pct"]
    if world.is_race and world.laps_remaining is not None:
        out["laps_remaining"] = world.laps_remaining
    rivals = []
    for pos in (me.lap.position - 1, me.lap.position + 1):
        c = world.car_at_position(pos)
        if c and c.status:
            rivals.append(
                {
                    "name": c.name,
                    "position": pos,
                    "tyres": names.compound_name(c.compound or 0),
                    "tyre_age": c.status.tyre_age_laps,
                    "stops": c.pit_stops,
                }
            )
    out["cars_around"] = rivals
    return out


def track(world: World) -> dict[str, Any]:
    """The circuit: corners (number, name, metres from the line), its character, and where the driver loses time."""
    from box_box.state import tracks

    s = world.session
    t = tracks.get(s.track_id) if s else None
    if s is None or t is None:
        return {"error": "No track data for this circuit."}
    out: dict[str, Any] = {
        "track": t.name,
        "length_m": t.length_m,
        "notes": list(t.notes),
        "corners": [{"turn": c.turn, "name": c.name, "at_m": round(c.at_m)} for c in t.corners],
    }
    me = world.player
    if me and me.lap and world.track_length:
        here = t.corner_after(me.lap.lap_distance, world.track_length)
        if here:
            out["next_corner"] = here.label
    if world.coach_note:
        out["where_time_is_lost"] = world.coach_note
    return out


def snapshot(world: World) -> str:
    """A short pit-wall summary sent with every question, so simple things need no tool call."""
    s = world.session
    me = world.player
    if s is None or me is None or me.lap is None:
        return "No live telemetry right now (game not running, in the menus, or UDP telemetry off)."
    parts = [
        f"{names.track_name(s.track_id)} {world.session_name}",
        f"P{me.lap.position}",
        f"lap {me.lap.lap_num}",
    ]
    if world.is_race and world.total_laps:
        parts.append(f"of {world.total_laps}")
    if me.status:
        parts.append(f"{names.compound_name(me.status.visual_compound)} {me.status.tyre_age_laps} laps old")
    if me.damage:
        parts.append(f"wear {round(sum(me.damage.tyre_wear) / 4)}%")
    g = gaps(world)
    if "ahead" in g:
        parts.append(f"{g['ahead']['name']} ahead +{g['ahead']['gap_s']}s")
    if "behind" in g:
        parts.append(f"{g['behind']['name']} behind -{g['behind']['gap_s']}s")
    if "my_best" in g and g["my_best"]:
        parts.append(f"best {g['my_best']}")
    parts.append(names.WEATHER.get(s.weather, ""))
    if s.safety_car_status in (1, 2):
        parts.append("SAFETY CAR" if s.safety_car_status == 1 else "VSC")
    status = {0: "in the garage", 1: "on a push lap", 2: "on an in lap", 3: "on an out lap", 4: "racing"}.get(
        me.lap.driver_status, ""
    )
    if me.in_pit_lane:
        status = "in the pit lane"
    parts.append(status)
    if me.lap.result_status == ResultStatus.FINISHED:
        # Otherwise the model sees "lap 29 of 29" and keeps saying "bring it home" after the flag.
        what = "RACE FINISHED" if world.is_race else "SESSION FINISHED"
        return f"{what}: we've taken the flag, final result P{me.lap.position}. " + ", ".join(p for p in parts if p)
    if world.is_race and world.chequered_flag:
        parts.append("chequered flag is out, finishing the last lap")
    elif world.is_race and world.laps_remaining == 1:
        parts.append("LAST LAP")
    return ", ".join(p for p in parts if p)


TOOLS: dict[str, Any] = {
    "session_info": session_info,
    "gaps": gaps,
    "standings": standings,
    "driver": driver,
    "tyres": tyres,
    "car_health": car_health,
    "fuel": fuel,
    "energy": energy,
    "weather": weather,
    "lap_times": lap_times,
    "strategy": strategy,
    "track": track,
}
