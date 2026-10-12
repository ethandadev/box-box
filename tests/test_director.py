from box_box.config import Settings
from box_box.engineer.director import RadioDirector
from box_box.engineer.radio import Priority, RadioCall, Verbosity
from tests.factories import Sim


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def call(key: str, priority: Priority = Priority.INFO, ttl: float = 20, category: str = "general", **kw) -> RadioCall:
    return RadioCall(key, priority, f"{key} text", 0.0, ttl, category=category, **kw)


def setup(**settings_patch):
    settings = Settings()
    for section, values in settings_patch.items():
        for k, v in values.items():
            setattr(getattr(settings, section), k, v)
    clock = Clock()
    sim = Sim([])
    return RadioDirector(lambda: settings, clock), clock, sim, settings


def cornering(sim: Sim) -> None:
    sim.car_telemetry(0, brake=0.8, steer=0.4, throttle=0.0, speed_kph=150)
    sim.tick()


def straight(sim: Sim) -> None:
    sim.car_telemetry(0, brake=0.0, steer=0.0, throttle=1.0, speed_kph=290)
    sim.tick()


def test_info_waits_for_a_straight_but_safety_does_not() -> None:
    d, clock, sim, _ = setup()
    cornering(sim)
    d.on_telemetry(sim.world)
    d.offer([call("gaps"), call("puncture", Priority.SAFETY)])
    first = d.poll(sim.world, None)
    assert first and first.call.key == "puncture"
    assert d.poll(sim.world, None) is None  # gaps waits: still in the corner
    straight(sim)
    d.on_telemetry(sim.world)
    clock.t += 0.2
    assert d.poll(sim.world, None) is None  # not long enough on the straight yet
    clock.t += 5
    nxt = d.poll(sim.world, None)
    assert nxt and nxt.call.key == "gaps"


def test_strategy_stops_waiting_for_a_straight_after_a_while() -> None:
    d, clock, sim, _ = setup()
    cornering(sim)
    d.on_telemetry(sim.world)
    d.offer([call("box", Priority.STRATEGY, ttl=20)])
    assert d.poll(sim.world, None) is None
    clock.t += 4.1
    d.on_telemetry(sim.world)  # still cornering
    got = d.poll(sim.world, None)
    assert got and got.call.key == "box"


def test_stale_calls_are_dropped() -> None:
    d, clock, sim, _ = setup()
    cornering(sim)
    d.on_telemetry(sim.world)
    d.offer([call("gaps", ttl=3)])
    clock.t += 4
    straight(sim)
    assert d.poll(sim.world, None) is None and d.dropped == 1


def test_same_key_replaces_older_call() -> None:
    d, clock, sim, _ = setup(callouts={"straights_only": False})
    d.offer([call("gaps")])
    d.offer([RadioCall("gaps", Priority.INFO, "newer", 0, 20)])
    got = d.poll(sim.world, None)
    assert got and got.call.text == "newer" and d.poll(sim.world, None) is None


def test_budget_and_spacing_ration_chatter() -> None:
    d, clock, sim, _ = setup(callouts={"straights_only": False}, engineer={"verbosity": "quiet"})
    d.on_telemetry(sim.world)
    d.offer([call(f"info{i}") for i in range(5)])
    said = []
    for _ in range(100):
        clock.t += 1
        got = d.poll(sim.world, None)
        if got:
            said.append(got.call.key)
    assert len(said) == 2  # quiet: two per lap


def test_verbosity_and_category_filters() -> None:
    d, clock, sim, settings = setup(callouts={"straights_only": False, "tyres": False})
    d.offer(
        [
            call("chatty", Priority.COLOUR, min_verbosity=Verbosity.CHATTY),
            call("tyres", category="tyres"),
            call("ok"),
        ]
    )
    got = d.poll(sim.world, None)
    assert got and got.call.key == "ok"
    clock.t += 10
    assert d.poll(sim.world, None) is None


def test_safety_interrupts_info_but_info_waits_for_speech_to_end() -> None:
    d, clock, sim, _ = setup(callouts={"straights_only": False})
    speaking = call("gaps")
    d.offer([call("lap")])
    assert d.poll(sim.world, speaking) is None
    d.offer([call("sc", Priority.SAFETY)])
    got = d.poll(sim.world, speaking)
    assert got and got.call.key == "sc" and got.interrupt


def test_push_to_talk_holds_the_engineer_but_not_replies() -> None:
    d, clock, sim, _ = setup(callouts={"straights_only": False})
    d.on_ptt(True)
    d.offer([call("gaps"), RadioCall("reply:1", Priority.STRATEGY, "Copy.", 0, 10, source="reply")])
    got = d.poll(sim.world, None)
    assert got and got.call.source == "reply"
    clock.t += 5
    assert d.poll(sim.world, None) is None  # still holding while the button is down
    d.on_ptt(False)
    clock.t += 1.5
    got = d.poll(sim.world, None)
    assert got and got.call.key == "gaps"


def test_quali_push_lap_only_lets_traffic_and_safety_through() -> None:
    d, clock, sim, _ = setup(callouts={"straights_only": False})
    sim.set_session(session_type=5)
    sim.lap(0, driver_status=1)
    sim.tick()
    d.offer([call("lap"), call("traffic:3", Priority.STRATEGY, category="traffic")])
    got = d.poll(sim.world, None)
    assert got and got.call.key == "traffic:3"
    clock.t += 10
    assert d.poll(sim.world, None) is None


def test_garage_counts_as_free_to_talk() -> None:
    d, clock, sim, _ = setup()
    sim.lap(0, driver_status=0)
    sim.car_telemetry(0, speed_kph=0, throttle=0.0)
    sim.tick()
    d.on_telemetry(sim.world)
    d.offer([call("greet")])
    assert d.poll(sim.world, None) is not None


def test_quali_lap_time_gets_through_just_after_the_line() -> None:
    d, clock, sim, _ = setup(callouts={"straights_only": False})
    sim.set_session(session_type=5)
    sim.lap(0, driver_status=1, current_lap_ms=2500)  # 2.5 s into the next push lap
    sim.tick()
    d.offer([call("lap", category="lap_feedback")])
    got = d.poll(sim.world, None)
    assert got and got.call.key == "lap"


def test_why_quiet_explains_the_hold() -> None:
    d, clock, sim, _ = setup(callouts={"straights_only": False})
    assert d.why_quiet(sim.world) is None
    d.on_ptt(True)
    d.offer([call("gaps")])
    assert "Push-to-talk is pressed" in (d.why_quiet(sim.world) or "")
    d.on_ptt(False)
    d.mute(120)
    assert "Radio silence" in (d.why_quiet(sim.world) or "")


def test_flashback_does_not_repeat_what_was_already_said() -> None:
    """Retrying a move with flashbacks re-triggered the same wing-damage and overtake calls four times."""
    d, clock, sim, _ = setup()
    straight(sim)
    d.on_telemetry(sim.world)
    clock.t += 5
    wing = RadioCall("wing", Priority.STRATEGY, "Heavy front wing damage. Box this lap.", 0.0, 30, category="damage")
    d.offer([wing])
    assert d.poll(sim.world, None)
    sim.world._rewind()
    d.on_telemetry(sim.world)
    clock.t += 30
    d.offer([wing, call("gaps", category="gaps")])
    nxt = d.poll(sim.world, None)
    assert nxt and nxt.call.key == "gaps"  # gaps are always fresh; the wing call isn't said again
    clock.t += 10
    assert d.poll(sim.world, None) is None


def test_same_sentence_is_not_said_twice_in_a_row() -> None:
    d, clock, sim, _ = setup()
    straight(sim)
    d.on_telemetry(sim.world)
    clock.t += 5
    d.offer([call("battery")])
    assert d.poll(sim.world, None)
    clock.t += 60
    d.offer([call("battery")])
    assert d.poll(sim.world, None) is None
