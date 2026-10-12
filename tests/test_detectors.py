from dataclasses import replace

from box_box.engineer.base import Detector
from box_box.engineer.car import CarDetector
from box_box.engineer.energy import EnergyDetector
from box_box.engineer.flags import FlagsDetector
from box_box.engineer.laps import LapFeedbackDetector
from box_box.engineer.race import RaceDetector
from box_box.engineer.radio import Priority, Verbosity
from box_box.engineer.session import SessionDetector
from box_box.engineer.weather import WeatherDetector
from box_box.telemetry.packets import WeatherSample
from tests.factories import SESSION, Sim

PRACTICE = replace(SESSION, session_type=1)
Q1 = replace(SESSION, session_type=5, time_left_s=900, duration_s=1080)


# ---- race ---------------------------------------------------------------------------------------


def test_lap_one_reports_places_gained() -> None:
    sim = Sim([RaceDetector()])
    sim.lap(0, grid_position=5, position=5)
    sim.tick()
    sim.lap(0, position=3)
    sim.complete_lap(0, 95_000)
    calls = sim.run(1)
    assert len(calls) == 1 and "P3" in calls[0].text and "2" in calls[0].text


def test_gap_updates_with_trend_every_two_laps() -> None:
    sim = Sim([RaceDetector()])
    sim.lap(0, position=2, grid_position=2, delta_to_leader_ms=2000)  # NORRIS (car 1) is P1 with 0
    sim.lap(1, position=1, delta_to_leader_ms=0)
    sim.lap(2, position=3, delta_to_leader_ms=3500)
    sim.complete_lap(0, 90_000)  # lap 1 -> lap-one call
    sim.run(1)
    gaps = []
    for lap_gap in (1.8, 1.6, 1.4):  # closing 0.2 s/lap on the leader
        sim.lap(0, delta_to_leader_ms=int(lap_gap * 1000))
        sim.complete_lap(0, 90_000)
        gaps += [c for c in sim.run(0.5) if c.key == "gaps"]
    assert len(gaps) == 2, [c.text for c in gaps]  # laps 2 and 4: every second lap
    text = gaps[1].text
    assert "Norris" in text and "1.4" in text and "closing" in text.lower() and "two tenths" in text
    assert "Leclerc" in text and "2.1" in text


def test_last_lap_and_win() -> None:
    sim = Sim([RaceDetector()], session=replace(SESSION, total_laps=3))
    sim.lap(0, lap_num=2)
    sim.tick()
    sim.complete_lap(0, 90_000)  # now on lap 3 of 3
    calls = sim.run(0.5)
    assert any(c.key == "last_lap" for c in calls)
    sim.lap(0, result_status=3, last_lap_ms=89_000)
    calls = sim.run(0.5)
    assert [c.key for c in calls] == ["finish"] and "P1" in calls[0].text


def test_car_ahead_pitting_and_my_stop() -> None:
    sim = Sim([RaceDetector()])
    sim.lap(0, position=3)
    sim.lap(2, position=2)
    sim.tick()
    sim.lap(2, pit_status=1)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "Leclerc" in calls[0].text and calls[0].priority == Priority.STRATEGY
    sim.lap(0, pit_status=1)
    sim.tick()
    sim.lap(0, pit_status=2, pit_stop_timer_ms=2400)
    sim.tick()
    sim.lap(0, pit_status=0, num_pit_stops=1)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "2.4" in calls[0].text


def test_overtake_is_debounced_and_cancelled_by_a_pass_back() -> None:
    sim = Sim([RaceDetector()])
    sim.lap(0, lap_num=5, position=4)
    sim.tick()
    sim.event("OVTK", vehicle_idx=0, other_vehicle_idx=3)
    sim.lap(0, position=3)
    assert sim.run(1) == []  # not yet: could be passed straight back
    sim.event("OVTK", vehicle_idx=3, other_vehicle_idx=0)  # Hamilton re-passes within 3 s
    sim.lap(0, position=4)
    assert sim.run(5) == []
    sim.event("OVTK", vehicle_idx=0, other_vehicle_idx=3)
    sim.lap(0, position=3)
    calls = sim.run(4)
    assert len(calls) == 1 and "P3" in calls[0].text


def test_no_position_commentary_through_our_own_pit_stop() -> None:
    """Places swap all through a stop; the stop call says where we rejoined."""
    sim = Sim([RaceDetector()])
    sim.lap(0, lap_num=16, position=1)
    sim.tick()
    sim.lap(0, pit_status=1)
    sim.event("OVTK", vehicle_idx=3, other_vehicle_idx=0)
    sim.lap(0, position=2)
    calls = sim.run(5)
    assert not [c for c in calls if c.key == "move"]


def test_pit_window_box_call() -> None:
    sim = Sim([RaceDetector()])
    sim.set_session(pit_window_ideal_lap=10, pit_window_latest_lap=12)
    sim.car_damage(0, tyre_wear=(45.0, 45.0, 40.0, 40.0))
    sim.lap(0, lap_num=7)
    sim.tick()
    sim.complete_lap(0, 90_000)  # now lap 8: window in 2 laps
    assert any(c.key == "pit_window" for c in sim.run(0.3))
    sim.complete_lap(0, 90_000)
    sim.run(0.3)
    sim.complete_lap(0, 90_000)  # now lap 10: box
    calls = sim.run(0.3)
    assert any(c.key == "box" and c.priority == Priority.STRATEGY for c in calls)


# ---- flags --------------------------------------------------------------------------------------


def test_safety_car_and_green() -> None:
    sim = Sim([FlagsDetector()])
    sim.run(0.5)
    sim.set_session(safety_car_status=1)
    calls = sim.run(0.5)
    assert len(calls) == 1 and calls[0].priority == Priority.SAFETY and "afety car" in calls[0].text
    sim.set_session(safety_car_status=0)
    calls = sim.run(0.5)
    assert len(calls) == 1 and "reen" in calls[0].text


def test_yellow_ahead_is_called_once_and_not_for_my_own_zone() -> None:
    sim = Sim([FlagsDetector()])
    sim.lap(0, lap_distance=1000)  # frac 0.2, zone 2
    sim.run(0.5)
    zones = list(sim.session.marshal_zones)
    zones[2] = zones[2].__class__(zones[2].start, 3)  # my zone goes yellow: I'm probably the cause
    sim.set_session(marshal_zones=tuple(zones))
    assert sim.run(3) == []
    zones[4] = zones[4].__class__(zones[4].start, 3)  # 0.4: ahead
    sim.set_session(marshal_zones=tuple(zones))
    calls = sim.run(5)
    assert len(calls) == 1 and "turn 8" in calls[0].text  # Silverstone: the corner, not just the sector


def test_time_penalty_event() -> None:
    sim = Sim([FlagsDetector()])
    sim.tick()
    sim.event("PENA", vehicle_idx=0, other_vehicle_idx=255, value=5, detail={"penalty_type": 4, "infringement_type": 4})
    calls = sim.run(0.2)
    assert len(calls) == 1 and "5 second" in calls[0].text and "contact" in calls[0].text


def test_lap_deleted_on_a_push_lap() -> None:
    sim = Sim([FlagsDetector()], session=PRACTICE)
    sim.lap(0, driver_status=1)
    sim.run(0.3)
    sim.lap(0, lap_invalid=True)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "deleted" in calls[0].text.lower()


# ---- car ----------------------------------------------------------------------------------------


def test_puncture_is_a_safety_call() -> None:
    sim = Sim([CarDetector()])
    sim.run(0.3)
    sim.car_damage(0, tyre_damage=(0, 0, 70, 0))
    calls = sim.run(0.3)
    assert len(calls) == 1 and calls[0].priority == Priority.SAFETY and "front left" in calls[0].text


def test_heavy_wing_damage_in_race_means_box() -> None:
    sim = Sim([CarDetector()])
    sim.run(0.3)
    sim.car_damage(0, front_left_wing=12)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "12" in calls[0].text
    sim.car_damage(0, front_left_wing=55)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "box" in calls[0].text.lower() and calls[0].priority == Priority.STRATEGY


def test_flashback_undoing_damage_is_not_new_damage() -> None:
    sim = Sim([CarDetector()])
    sim.car_damage(0, front_left_wing=30)
    sim.run(0.3)
    sim.event("FLBK", value=5.0, detail={"frame": 10})
    sim.car_damage(0, front_left_wing=0)
    sim.run(0.3)
    sim.car_damage(0, front_left_wing=30)  # crash again after the flashback: that IS new
    calls = sim.run(0.3)
    assert len(calls) == 1


def test_overheating_rears_after_sustained_heat() -> None:
    sim = Sim([CarDetector()])
    sim.lap(0, lap_num=5)
    sim.car_telemetry(0, tyre_inner_temps=(125, 124, 100, 100))
    assert sim.run(5) == []  # a spike isn't a trend
    calls = sim.run(5)
    assert len(calls) == 1 and "Rears" in calls[0].text
    assert sim.run(30) == []  # no nagging


def test_tyre_wear_thresholds() -> None:
    sim = Sim([CarDetector()])
    sim.car_damage(0, tyre_wear=(52.0, 50.0, 49.0, 51.0))
    sim.complete_lap(0, 90_000)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "50" in calls[0].text
    sim.car_damage(0, tyre_wear=(72.0, 70.0, 71.0, 71.0))
    sim.complete_lap(0, 90_000)
    calls = sim.run(0.3)
    assert len(calls) == 1 and calls[0].priority == Priority.STRATEGY


# ---- laps ---------------------------------------------------------------------------------------


def test_practice_pb_then_slower_lap_names_the_sector() -> None:
    sim = Sim([LapFeedbackDetector()], session=PRACTICE)
    sim.lap(0, driver_status=1, lap_num=2)
    sim.tick()
    sim.complete_lap(0, 90_000, s1=30_000, s2=30_000)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "1:30.0" in calls[0].text and "best" in calls[0].text.lower()
    sim.complete_lap(0, 90_600, s1=30_000, s2=30_550)  # sector 2 +0.55
    calls = sim.run(0.3)
    assert len(calls) == 1 and "sector 2" in calls[0].text and "six tenths" in calls[0].text


def test_flashback_across_the_line_is_not_a_new_lap() -> None:
    """Rewinding over the line steps the lap number back and 'last lap' to an older time: no lap call,
    and the abandoned lap stops counting as the best."""
    sim = Sim([LapFeedbackDetector()], session=PRACTICE)
    sim.lap(0, driver_status=1, lap_num=2)
    sim.tick()
    sim.complete_lap(0, 91_000)
    sim.run(0.3)
    sim.complete_lap(0, 90_000)  # a PB on lap 3...
    sim.run(0.3)
    sim.event("FLBK", value=5.0, detail={"frame": 10})
    sim.lap(0, lap_num=3, last_lap_ms=91_000)  # ...that a flashback takes away
    assert sim.run(0.5) == []
    assert sim.world.player.best_lap_ms == 91_000


def test_out_and_in_laps_get_no_feedback() -> None:
    sim = Sim([LapFeedbackDetector()], session=PRACTICE)
    sim.lap(0, driver_status=3)
    sim.tick()
    sim.complete_lap(0, 120_000)
    assert sim.run(0.3) == []


# ---- session ------------------------------------------------------------------------------------


def test_practice_greeting_when_leaving_the_garage() -> None:
    sim = Sim([SessionDetector()], session=PRACTICE)
    sim.lap(0, driver_status=0)
    assert sim.run(2) == []
    sim.lap(0, driver_status=3)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "Ethan" in calls[0].text and "Silverstone" in calls[0].text


def test_quali_drop_zone() -> None:
    sim = Sim([SessionDetector()], session=Q1, cars=22)
    sim.lap(0, driver_status=1, position=18)
    sim.tick()
    sim.complete_lap(0, 90_000)
    calls = [c for c in sim.run(0.3) if c.key == "quali_pos"]
    assert len(calls) == 1 and "P18" in calls[0].text
    sim.lap(0, position=12)
    sim.complete_lap(0, 89_000)
    calls = [c for c in sim.run(0.3) if c.key == "quali_pos"]
    assert len(calls) == 1 and "P12" in calls[0].text and calls[0].priority == Priority.INFO


def test_time_left_markers() -> None:
    sim = Sim([SessionDetector()], session=replace(PRACTICE, time_left_s=320))
    sim.lap(0, driver_status=0)
    sim.run(0.3)
    sim.set_session(time_left_s=299)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "5 minutes" in calls[0].text and calls[0].min_verbosity == Verbosity.NORMAL


# ---- weather & energy ---------------------------------------------------------------------------


def test_rain_forecast_then_rain() -> None:
    sim = Sim([WeatherDetector()])
    sim.set_session(forecast=(WeatherSample(15, 5, 0, 30, 22, 10), WeatherSample(15, 10, 3, 28, 20, 70)))
    calls = sim.run(1)
    assert len(calls) == 1 and "10 minutes" in calls[0].text and "70" in calls[0].text
    assert sim.run(5) == []  # not repeated
    sim.set_session(weather=3)
    calls = sim.run(0.3)
    assert len(calls) == 1 and "light rain" in calls[0].text.lower()


def test_overtake_mode_available() -> None:
    sim = Sim([EnergyDetector()])
    sim.lap(0, lap_num=4, position=2)
    sim.run(0.3)
    sim.car_t2(0, overtake_available=True)
    calls = sim.run(1)
    assert len(calls) == 1 and "vertake" in calls[0].text


# ---- robustness ---------------------------------------------------------------------------------


class Broken(Detector):
    name = "broken"

    def check(self, ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")


def test_a_crashing_detector_doesnt_stop_the_others() -> None:
    sim = Sim([Broken(), CarDetector()])
    sim.run(0.3)
    sim.car_damage(0, tyre_damage=(0, 0, 70, 0))
    assert len(sim.run(0.3)) == 1


def test_worn_tyre_creeping_past_60_percent_is_not_a_puncture() -> None:
    """From a real race: a 20-lap-old hard's damage climbed 57 -> 60 and the engineer called a puncture."""
    sim = Sim([CarDetector()])
    sim.car_damage(0, tyre_damage=(45, 37, 59, 37), tyre_wear=(44.0, 36.0, 58.0, 36.0))
    sim.run(0.3)
    sim.car_damage(0, tyre_damage=(45, 37, 60, 37), tyre_wear=(44.5, 36.0, 59.0, 36.0))
    assert not [c for c in sim.run(0.3) if c.key == "puncture"]
