"""Diff, brake bias, ERS mode and fuel map advice: decoded from the game, asked for, and called on its own."""

import random
import struct

import pytest

from box_box.brain import intents, tools
from box_box.engineer import advice
from box_box.engineer.car import CarDetector
from box_box.engineer.energy import EnergyDetector
from box_box.engineer.handling import HandlingDetector
from box_box.engineer.radio import no_data
from box_box.state import tracks
from box_box.telemetry.packets import LAYOUTS, CarSetup, Decoder, PacketId, WheelSlip
from tests.builders import header
from tests.factories import Sim

SETUP = CarSetup(46, 33, 75, 35, 100, 56, 50, 10.0)


def sim_with_setup(detectors=None) -> Sim:  # type: ignore[no-untyped-def]
    sim = Sim(detectors or [])
    sim.lap(0, lap_num=6)
    sim.tick()
    sim.world.player.setup = SETUP  # type: ignore[union-attr]
    return sim


@pytest.mark.parametrize("fmt", [2025, 2026])
def test_setups_and_wheel_slip_decode(fmt: int) -> None:
    lay = LAYOUTS[fmt]
    car = struct.pack("<BBBBffffBBBBBBBBBffffBf", 46, 33, 80, 40, -3.0, -1.5, 0.05, 0.2, 30, 10, 12, 8, 20, 50,
                      100, 57, 50, 23.5, 23.5, 21.0, 21.0, 0, 12.5)  # fmt: skip
    data = header(PacketId.CAR_SETUPS, fmt=fmt) + car * lay.max_cars + struct.pack("<f", 0.0)
    assert len(data) == lay.packet_sizes[PacketId.CAR_SETUPS]
    p = Decoder(Decoder.DEFAULT_WANTED).decode(data)
    assert p is not None and p.data[0] == CarSetup(46, 33, 80, 40, 100, 57, 50, 12.5)  # type: ignore[index]

    floats = [0.0] * 12 + [30.0, 30.5, 29.0, 29.0] + [0.2, 0.25, -0.01, -0.3] + [0.0] * 41
    data = header(PacketId.MOTION_EX, fmt=fmt) + struct.pack(f"<{len(floats)}f", *floats)
    assert len(data) == lay.packet_sizes[PacketId.MOTION_EX]
    p = Decoder(Decoder.DEFAULT_WANTED).decode(data)
    assert p is not None and isinstance(p.data, WheelSlip)
    assert p.data.slip_ratio == pytest.approx((0.2, 0.25, -0.01, -0.3))


def test_wheelspin_lowers_the_diff_for_that_corner() -> None:
    sim = sim_with_setup()
    text = advice.answer("i keep getting wheelspin any diff advice out of turn 14", sim.world, random.Random(1))
    assert text and "65" in text and "turn 14" in text and "diff" in text.lower()


def test_diff_question_without_trouble_leaves_it() -> None:
    text = advice.answer("any diff advice", sim_with_setup().world, random.Random(2))
    assert text and "75" in text


@pytest.mark.parametrize(
    ("said", "bias"),
    [("i'm locking the fronts into turn 1", "54"), ("rears are locking", "58"), ("won't turn in", "55")],
)
def test_brake_bias_follows_the_symptom(said: str, bias: str) -> None:
    text = advice.answer(said, sim_with_setup().world, random.Random(3))
    assert text and bias in text and "bias" in text.lower()


def test_low_battery_on_hotlap_says_energy_mode_medium() -> None:
    sim = sim_with_setup()
    sim.car_status(0, ers_store_j=0.4e6, ers_deploy_mode=2)
    sim.tick()
    for seed in range(5):
        text = advice.answer("what ers mode", sim.world, random.Random(seed))
        assert text and "medium" in text.lower()


def test_fuel_short_on_standard_says_lean() -> None:
    sim = sim_with_setup()
    sim.car_status(0, fuel_remaining_laps=-1.2, fuel_mix=1)
    sim.tick()
    text = advice.answer("fuel map", sim.world, random.Random(4))
    assert text and "lean" in text.lower() and "1.2" in text


def test_fuel_short_call_names_the_map() -> None:
    sim = Sim([CarDetector()])
    sim.lap(0, lap_num=5)
    sim.tick()
    sim.car_status(0, fuel_remaining_laps=-1.0, fuel_mix=1)
    sim.complete_lap(0, 90_000)
    calls = sim.tick()
    fuel = [c for c in calls if c.key == "fuel"]
    assert fuel and "lean" in fuel[0].text.lower() and "lift and coast" in fuel[0].text.lower()


def test_battery_flat_in_hotlap_mode_gets_a_call() -> None:
    sim = Sim([EnergyDetector()])
    sim.lap(0, lap_num=5)
    sim.car_status(0, ers_store_j=0.3e6, ers_deploy_mode=2)
    calls = sim.tick()
    assert [c.key for c in calls] == ["ers_mode"]
    assert "medium" in calls[0].text.lower()
    assert sim.tick() == []  # once, not every tick


def test_same_wheelspin_lap_after_lap_gets_diff_advice() -> None:
    sim = sim_with_setup([HandlingDetector()])
    w = sim.world
    corner, at = tracks.get(7).corners_on(5000)[4]  # type: ignore[union-attr]
    spin, grip = WheelSlip((0.3, 0.3, 0.0, 0.0), (40.0,) * 4), WheelSlip((0.0,) * 4, (40.0,) * 4)
    sim.car_telemetry(0, speed_kph=120, throttle=1.0, brake=0.0)
    calls = []
    for lap in (6, 7, 8):
        sim.lap(0, lap_num=lap, lap_distance=at + 60)
        w.wheel_slip = spin
        calls += sim.tick()
        w.wheel_slip = grip
        calls += sim.run(1.0)
    setup = [c for c in calls if c.key == "setup"]
    assert len(setup) == 1
    assert "65" in setup[0].text and corner.label in setup[0].text
    assert sum(s.kind == "wheelspin" for s in w.slides) == 3


def test_driver_asks_and_gets_it_after_a_look() -> None:
    sim = sim_with_setup()
    ans = intents.answer("I keep getting wheelspin, any diff advice?", sim.world, None, "Ethan")  # type: ignore[arg-type]
    assert ans and ans.instant and ans.preface and ans.delay_s > 0
    assert "diff" in ans.text.lower()


def test_setup_tool_for_the_ai() -> None:
    info = tools.car_setup(sim_with_setup().world)
    assert info["diff_on_throttle_pct"] == 75 and info["brake_bias_front_pct"] == 56
    assert info["ers_mode"] == "medium" and info["fuel_mix"] == "standard"


def test_no_data_said_like_a_pit_wall() -> None:
    said = {no_data(random.Random(i), "setup") for i in range(60)}
    assert len(said) > 8
    assert any("sensor" in s for s in said) and any(s.startswith("We are checking") for s in said)
    assert all("{" not in s for s in said)


def test_off_track_spin_and_changed_diff_dont_count() -> None:
    sim = sim_with_setup([HandlingDetector()])
    w = sim.world
    _, at = tracks.get(7).corners_on(5000)[4]  # type: ignore[union-attr]
    sim.lap(0, lap_distance=at + 60)
    sim.car_telemetry(0, speed_kph=120, throttle=1.0, brake=0.0, surface_types=(7, 7, 0, 0))
    w.wheel_slip = WheelSlip((0.4, 0.4, 0.0, 0.0), (40.0,) * 4)
    sim.tick()
    assert w.slides == []  # rears on the grass
    sim.car_telemetry(0, surface_types=(0, 0, 0, 0))
    sim.tick()
    assert [s.kind for s in w.slides] == ["wheelspin"]
    w.player.setup = CarSetup(46, 33, 65, 35, 100, 56, 50, 10.0)  # type: ignore[union-attr]
    w.wheel_slip = WheelSlip((0.0,) * 4, (40.0,) * 4)
    sim.tick()
    assert w.slides == []  # he took diff off: start counting again


def test_traction_control_isnt_a_diff_question() -> None:
    assert advice.answer("should i turn traction control off", sim_with_setup().world, random.Random(5)) is None


@pytest.mark.parametrize("said", ["the rear is locking into turn 1", "rear's locking", "rear wheels locking up"])
def test_rear_lock_phrasings_move_the_bias_forward(said: str) -> None:
    text = advice.answer(said, sim_with_setup().world, random.Random(6))
    assert text and "forward" in text.lower()


def test_damage_questions_match_whole_words() -> None:
    from box_box.engineer.damage import parts_named

    assert parts_named("engineer, any damage?") == ()
    assert set(parts_named("check the front wing and floor")) == {"front_left_wing", "front_right_wing", "floor"}
