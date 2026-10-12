"""The strategist: grid tyre brief, plan A/B on tyre life, the safety-car window, rival intel, feedback."""

import asyncio
from dataclasses import replace

from box_box.brain import intents
from box_box.brain.conversation import Conversation, is_question
from box_box.brain.llm import Brain
from box_box.config import Settings
from box_box.engineer.race import RaceDetector
from box_box.engineer.radio import Phrasebook, Priority
from box_box.engineer.strategy import StrategyDetector, tyre_offset
from tests.factories import SESSION, Sim
from tests.test_brain import FakeActions


def racing(sim: Sim) -> None:
    sim.event("LGOT")
    sim.tick()


def wear_laps(sim: Sim, start_wear: float, per_lap: float, laps: int, start_age: int = 1) -> list:
    """Complete `laps` laps for the player with tyre wear rising `per_lap` each lap."""
    calls = []
    for i in range(laps):
        w = start_wear + per_lap * (i + 1)
        sim.car_damage(0, tyre_wear=(w, w, w, w))
        sim.car_status(0, tyre_age_laps=start_age + i + 1)
        sim.complete_lap(0, 90_000)
        calls += sim.run(0.3)
    return calls


def test_grid_tyre_brief_names_the_odd_ones_out() -> None:
    sim = Sim([StrategyDetector()], cars=8)
    for i in range(8):
        sim.lap(i, position=i + 1, grid_position=i + 1)
    sim.car_status(3, visual_compound=18)  # Hamilton P4 on hards
    sim.car_status(5, visual_compound=16)  # Piastri P6 on softs
    calls = sim.run(0.3)
    brief = [c for c in calls if c.key == "grid_tyres"]
    assert len(brief) == 1
    text = brief[0].text
    assert "most of the field on mediums" in text.lower()
    assert "Hamilton P4" in text and "hards" in text and "Piastri P6" in text and "We're on mediums" in text
    assert sim.run(1) == []  # once


def test_robust_tyres_then_plan_b_when_deg_bites() -> None:
    sim = Sim([StrategyDetector()], session=replace(SESSION, total_laps=30))
    racing(sim)
    sim.lap(0, lap_num=2)
    sim.tick()
    calls = wear_laps(sim, 5, 1.5, 5)  # 1.5%/lap with 24 to go: lasts ~38 laps -> fine
    robust = [c for c in calls if c.key == "tyre_plan"]
    assert len(robust) == 1 and robust[0].priority == Priority.INFO
    calls = wear_laps(sim, 12.5, 6.0, 3, start_age=6)  # now 6%/lap: dead in a few laps
    plan_b = [c for c in calls if c.key == "tyre_plan"]
    assert plan_b and "plan B" in plan_b[0].text and plan_b[0].priority == Priority.STRATEGY


def test_extend_the_stint_when_tyres_are_fresh_at_the_window() -> None:
    sim = Sim([StrategyDetector(), RaceDetector()], session=replace(SESSION, total_laps=40))
    sim.set_session(pit_window_ideal_lap=10, pit_window_latest_lap=15)
    racing(sim)
    sim.lap(0, lap_num=3)
    sim.tick()
    calls = wear_laps(sim, 4, 1.5, 5, start_age=2)  # lap 8 now, ~12% worn, 1.5%/lap
    ext = [c for c in calls if c.key == "tyre_plan" and "plus" in c.text]
    assert ext, [c.text for c in calls]
    calls = wear_laps(sim, 12, 1.5, 2, start_age=7)  # lap 10: the window, but no box call
    assert not any(c.key == "box" for c in calls)


def test_safety_car_window_then_box_under_the_safety_car() -> None:
    sim = Sim([StrategyDetector()], session=replace(SESSION, total_laps=30))
    sim.set_session(pit_window_ideal_lap=14, pit_window_latest_lap=18)
    racing(sim)
    sim.lap(0, lap_num=7)
    sim.tick()
    calls = wear_laps(sim, 10, 2.0, 1, start_age=7)  # lap 8: within 6 of the window
    assert any(c.key == "sc_window" for c in calls)
    sim.set_session(safety_car_status=1)
    calls = sim.run(0.3)
    box = [c for c in calls if c.key == "box"]
    assert len(box) == 1 and box[0].priority == Priority.STRATEGY and "Box" in box[0].text
    sim.set_session(safety_car_status=0)
    sim.run(0.3)
    sim.set_session(safety_car_status=1)
    assert not any(c.key == "box" for c in sim.run(0.3))  # once per stint


def test_rival_yet_to_stop_on_old_tyres() -> None:
    sim = Sim([StrategyDetector()], session=replace(SESSION, total_laps=40))
    racing(sim)
    sim.lap(0, position=3, lap_num=20, num_pit_stops=1)
    sim.lap(2, position=2, num_pit_stops=0)
    sim.lap(1, position=1)
    sim.car_status(0, tyre_age_laps=4)
    sim.car_status(2, tyre_age_laps=19, visual_compound=18)
    sim.tick()
    sim.complete_lap(0, 90_000)
    calls = [c for c in sim.run(0.3) if c.key.startswith("rival")]
    assert len(calls) == 1 and "Leclerc" in calls[0].text and "19" in calls[0].text and "hards" in calls[0].text


def test_tyre_offset_note() -> None:
    sim = Sim([])
    sim.car_status(0, tyre_age_laps=3, visual_compound=17)
    sim.car_status(1, tyre_age_laps=9, visual_compound=18)
    sim.car_status(2, tyre_age_laps=4, visual_compound=17)
    sim.tick()
    me, nor, lec = sim.world.cars[0], sim.world.cars[1], sim.world.cars[2]
    assert tyre_offset(me, nor) == "He's on hards, six laps older."
    assert tyre_offset(me, lec) is None


def test_practice_asks_about_the_balance_and_remembers_the_answer() -> None:
    sim = Sim([StrategyDetector()], session=replace(SESSION, session_type=1))
    sim.complete_lap(0, 90_000)
    sim.run(0.3)
    sim.complete_lap(0, 89_500)
    asks = [c for c in sim.run(0.3) if c.key.startswith("ask:")]
    assert len(asks) == 1 and asks[0].key == "ask:balance"

    settings = Settings()
    acts = FakeActions()
    conv = Conversation(Brain(lambda: settings, lambda: ""), acts, lambda: settings)
    conv.expect_feedback("balance")

    async def say(text: str) -> list[str]:
        return [s async for s in conv.handle(text, sim.world)]

    reply = asyncio.run(say("bit of understeer in the slow corners, rears are fine"))
    assert reply and reply[0].startswith(("Copy", "Okay"))
    assert acts.log[-1][0] == "remember" and "Balance feedback" in acts.log[-1][1] and "understeer" in acts.log[-1][1]
    # The next thing said is a normal question again.
    assert asyncio.run(say("what's the gap")) != reply


def test_a_question_is_not_mistaken_for_feedback() -> None:
    assert is_question("how many laps left?") and is_question("What's the gap") and not is_question("feels good")


def test_say_again_and_whats_the_plan() -> None:
    class Acts(FakeActions):
        def last_radio(self) -> str | None:
            return "Box this lap, box this lap."

    sim = Sim([], session=replace(SESSION, total_laps=30))
    sim.set_session(pit_window_ideal_lap=12, pit_window_latest_lap=15)
    sim.lap(0, lap_num=5)
    sim.tick()
    assert intents.answer("say again?", sim.world, Acts(), "Ethan").text == "Box this lap, box this lap."  # type: ignore[union-attr]
    plan = intents.answer("what's the plan", sim.world, Acts(), "Ethan")
    assert plan and plan.text.startswith("Plan A. Box around lap 12")


def test_openers_keep_proper_names_capitalised() -> None:
    say = Phrasebook(seed=1)
    out = {say.lead("Leclerc 1.2 ahead.", me="Ethan", p=1.0) for _ in range(20)}
    assert all("Leclerc" in o for o in out)
    out = {say.lead("Gap to Leclerc 1.2.", me="Ethan", p=1.0) for _ in range(20)}
    assert all("gap to Leclerc" in o for o in out)


def test_second_quali_run_gets_its_own_call() -> None:
    from box_box.engineer.session import SessionDetector

    sim = Sim([SessionDetector()], session=replace(SESSION, session_type=5, time_left_s=600))
    sim.lap(0, driver_status=0)
    sim.tick()
    sim.lap(0, driver_status=3)
    first = sim.run(0.3)
    assert [c.key for c in first] == ["greet"]  # the radio check
    sim.lap(0, driver_status=0)
    sim.run(0.3)
    sim.set_session(time_left_s=420)
    sim.lap(0, driver_status=3)
    second = sim.run(0.3)
    assert len(second) == 1 and "second run" in second[0].text.lower() and "7 minutes" in second[0].text


def test_back_in_the_rhythm_after_a_scruffy_lap() -> None:
    sim = Sim([StrategyDetector()], session=replace(SESSION, total_laps=30))
    racing(sim)
    for _ in range(6):
        sim.complete_lap(0, 90_000)
        sim.run(0.3)
    sim.complete_lap(0, 93_200)  # off by 3.2 s
    calls = [c for c in sim.run(0.3) if c.key == "rhythm"]
    assert len(calls) == 1 and "rhythm" in calls[0].text.lower()


def test_leading_comfortably_late_on() -> None:
    sim = Sim([StrategyDetector()], session=replace(SESSION, total_laps=12))
    racing(sim)
    sim.lap(0, lap_num=5, position=1, delta_to_leader_ms=0)
    sim.lap(1, position=2, delta_to_leader_ms=7400)
    sim.tick()
    sim.complete_lap(0, 90_000)
    calls = [c for c in sim.run(0.3) if c.key == "lead"]
    assert len(calls) == 1 and "7 seconds" in calls[0].text and "nothing to prove" in calls[0].text


def test_fresher_tyres_than_the_car_ahead() -> None:
    sim = Sim([StrategyDetector()], session=replace(SESSION, total_laps=40))
    racing(sim)
    sim.lap(0, position=3, lap_num=25, num_pit_stops=1, delta_to_leader_ms=6000)
    sim.lap(2, position=2, num_pit_stops=1, delta_to_leader_ms=3000)
    sim.lap(1, position=1, delta_to_leader_ms=0)
    sim.car_status(0, tyre_age_laps=3)
    sim.car_status(2, tyre_age_laps=14)
    sim.tick()
    sim.complete_lap(0, 90_000)
    calls = [c for c in sim.run(0.3) if c.key.startswith("rival")]
    assert len(calls) == 1 and "Leclerc" in calls[0].text and "11 laps fresher" in calls[0].text


def test_quali_margins_in_thousandths_and_elimination() -> None:
    from box_box.engineer.radio import fine_delta
    from box_box.engineer.session import SessionDetector

    assert fine_delta(0.011) == "11 thousandths" and fine_delta(0.3) == "three tenths"
    sim = Sim([SessionDetector()], cars=20, session=replace(SESSION, session_type=5))
    sim.lap(0, position=17)
    sim.tick()
    sim.event("SEND")
    calls = [c for c in sim.run(0.3) if c.key == "session_end"]
    assert len(calls) == 1 and "P17" in calls[0].text and ("out" in calls[0].text or "through" in calls[0].text)


def test_softer_tyre_behind_means_higher_deg() -> None:
    sim = Sim([])
    sim.car_status(0, tyre_age_laps=10, visual_compound=18)
    sim.car_status(3, tyre_age_laps=4, visual_compound=16)
    sim.tick()
    me, ham = sim.world.cars[0], sim.world.cars[3]
    assert tyre_offset(me, ham, behind=True) == "He's on softs, six laps newer. He'll have higher deg."


def test_cold_tyres_behind_the_safety_car_and_new_tyre_briefing() -> None:
    sim = Sim([StrategyDetector(), RaceDetector()], session=replace(SESSION, total_laps=30))
    racing(sim)
    sim.lap(0, lap_num=10, position=3)
    sim.car_telemetry(0, tyre_inner_temps=(70, 70, 72, 72))
    sim.set_session(safety_car_status=1)
    calls = [c for c in sim.run(0.5) if c.key == "sc_temps"]
    assert len(calls) == 1 and "weav" in calls[0].text.lower()
    sim.set_session(safety_car_status=0)
    sim.car_telemetry(0, tyre_inner_temps=(90, 90, 90, 90))
    sim.lap(0, pit_status=1)
    sim.tick()
    sim.lap(0, pit_status=2, pit_stop_timer_ms=2500)
    sim.car_status(0, visual_compound=18, tyre_age_laps=0)
    sim.tick()
    sim.lap(0, pit_status=0, num_pit_stops=1)
    done = [c for c in sim.run(0.3) if c.key == "pit_done"]
    assert len(done) == 1 and "2.5" in done[0].text and "hards" in done[0].text
