import pytest

from box_box.engineer.radio import Phrasebook, Priority, RadioCall
from box_box.engineer.traffic import TrafficDetector
from box_box.state.model import World
from box_box.state.track import in_zone, road_offset
from box_box.telemetry.packets import Decoder, DriverStatus
from tests.builders import Car, Scenario

FLYING, IN_LAP, OUT_LAP = DriverStatus.FLYING_LAP, DriverStatus.IN_LAP, DriverStatus.OUT_LAP


def run(scenario: Scenario, seconds: float, dt: float = 0.1) -> list[RadioCall]:
    decoder, world, detector = Decoder(), World(), TrafficDetector(phrasebook=Phrasebook(seed=1))
    calls: list[RadioCall] = []
    for data in scenario.setup():
        world.apply(decoder.decode(data))
    steps = round(seconds / dt)
    for _ in range(steps + 1):
        for data in scenario.frame():
            world.apply(decoder.decode(data))
        calls += detector.update(world)
        scenario.step(dt)
    return calls


def test_road_offset_wraps_the_line() -> None:
    assert road_offset(4950, 50, 5000) == pytest.approx(100)  # just across the line, ahead
    assert road_offset(50, 4950, 5000) == pytest.approx(-100)  # behind, on the previous lap's end
    assert in_zone(0.98, (0.95, 0.15)) and in_zone(0.05, (0.95, 0.15)) and not in_zone(0.5, (0.95, 0.15))


def test_outlap_car_closing_from_behind_gets_two_calls_then_silence() -> None:
    # I'm cruising on an out lap at 150; Verstappen is 1 km back on a push lap at 300.
    sc = Scenario([Car("Ethan", 2000, 150, OUT_LAP), Car("Max VERSTAPPEN", 1000, 300, FLYING)])
    calls = run(sc, 30)
    assert [c.priority for c in calls] == [Priority.STRATEGY, Priority.SAFETY]
    assert "Verstappen" in calls[0].text and "seconds" in calls[0].text
    assert "Verstappen" in calls[1].text
    # Early call lands when the gap is about 8 s (at 300 kph that's ~667 m); they close at 150 kph = 41.7 m/s.
    assert calls[0].created_at == pytest.approx((1000 - 667) / 41.67, abs=0.3)


def test_car_behind_on_push_lap_across_the_line() -> None:
    sc = Scenario([Car("Ethan", 100, 150, OUT_LAP), Car("Lando NORRIS", 4700, 300, FLYING)])
    calls = run(sc, 3)
    assert len(calls) == 1 and "Norris" in calls[0].text


def test_no_call_when_both_pushing_or_car_not_closing() -> None:
    both_pushing = Scenario([Car("Ethan", 2000, 290, FLYING), Car("Max VERSTAPPEN", 1500, 300, FLYING)])
    assert run(both_pushing, 20) == []
    pulling_away = Scenario([Car("Ethan", 2000, 300, OUT_LAP), Car("Max VERSTAPPEN", 1700, 250, FLYING)])
    assert run(pulling_away, 20) == []


def test_slow_car_ahead_on_my_push_lap() -> None:
    sc = Scenario([Car("Ethan", 1000, 300, FLYING), Car("Fernando ALONSO", 1600, 140, OUT_LAP)])
    calls = run(sc, 20)
    assert len(calls) == 1
    assert calls[0].text == "Traffic ahead, Alonso on an out lap, 6 seconds."
    assert calls[0].priority == Priority.STRATEGY


def test_silent_in_races_and_for_cars_in_the_pits() -> None:
    race = Scenario([Car("Ethan", 2000, 150, OUT_LAP), Car("Max VERSTAPPEN", 1000, 300, FLYING)], session_type=15)
    assert run(race, 30) == []
    pitlane = Scenario([Car("Ethan", 2000, 150, OUT_LAP), Car("Max VERSTAPPEN", 1000, 300, FLYING, pit=1)])
    assert run(pitlane, 30) == []


def test_encounter_resets_after_the_car_passes() -> None:
    # Two pushing cars come by one after another: each gets its own calls.
    sc = Scenario(
        [
            Car("Ethan", 3000, 120, IN_LAP),
            Car("Max VERSTAPPEN", 2200, 300, FLYING),
            Car("Charles LECLERC", 1200, 300, FLYING),
        ]
    )
    calls = run(sc, 40)
    assert sum("Verstappen" in c.text for c in calls) == 2
    assert sum("Leclerc" in c.text for c in calls) == 2
