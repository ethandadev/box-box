"""Traffic management in practice and qualifying: the "car behind on a push lap" and "traffic ahead" calls.

Each encounter with another car is called at most once per stage (early warning, then close), and the
encounter resets once they're past you or have dropped well back, so the engineer never nags.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from box_box.engineer.radio import Phrasebook, Priority, RadioCall, spoken_seconds
from box_box.state.model import CarState, World
from box_box.state.track import gap_seconds, road_offset, time_to_catch
from box_box.telemetry.packets import DriverStatus

NOT_PUSHING = frozenset({DriverStatus.OUT_LAP, DriverStatus.IN_LAP, DriverStatus.ON_TRACK})


@dataclass
class TrafficConfig:
    behind_early_s: float = 8.0  # first heads-up for a pushing car behind
    behind_close_s: float = 3.0  # "let them by"
    ahead_s: float = 6.0  # slow car ahead while you're on a push lap
    reset_s: float = 15.0  # encounter over once the gap grows past this
    min_speed_kph: float = 60.0  # ignore cars crawling out of the garage or spun


@dataclass
class _Encounter:
    stages: set[str] = field(default_factory=set)


class TrafficDetector:
    def __init__(self, config: TrafficConfig | None = None, phrasebook: Phrasebook | None = None) -> None:
        self.cfg = config or TrafficConfig()
        self.say = phrasebook or Phrasebook()
        self._encounters: dict[int, _Encounter] = {}
        self._timeline = -1

    def update(self, world: World) -> list[RadioCall]:
        if world.timeline != self._timeline:
            self._encounters.clear()  # flashback/new session: forget who we've already called
            self._timeline = world.timeline
        if world.is_race or world.track_length <= 0:
            return []
        me = world.player
        if me is None or not me.on_track or me.lap is None:
            return []

        calls: list[RadioCall] = []
        for car in world.others():
            if not car.on_track or car.speed_kph < self.cfg.min_speed_kph:
                self._encounters.pop(car.index, None)
                continue
            call = self._check(world, me, car)
            if call:
                calls.append(call)
        return calls

    def _check(self, world: World, me: CarState, car: CarState) -> RadioCall | None:
        assert me.lap and car.lap
        offset = road_offset(me.lap.lap_distance, car.lap.lap_distance, world.track_length)
        mine, theirs = me.driver_status, car.driver_status
        enc = self._encounters.setdefault(car.index, _Encounter())
        now = world.session_time

        if offset < 0 and theirs == DriverStatus.FLYING_LAP and mine in NOT_PUSHING:
            gap_m = -offset
            gap = gap_seconds(gap_m, car.speed_kph)
            closing = time_to_catch(gap_m, car.speed_kph, me.speed_kph) is not None
            if gap is None or gap > self.cfg.reset_s:
                enc.stages.clear()
                return None
            if not closing:
                return None
            if gap <= self.cfg.behind_close_s and "behind_close" not in enc.stages:
                enc.stages |= {"behind_close", "behind_early"}
                text = self.say.pick(
                    "behind_close",
                    [
                        "{name} right behind, push lap. Let {name} by.",
                        "Mirrors. {name} on a push lap, right behind you.",
                        "{name} is on it, right behind. Stay off the line.",
                    ],
                    name=car.name,
                )
                return RadioCall(f"traffic:{car.index}", Priority.SAFETY, text, now, now + 2.0)
            if gap <= self.cfg.behind_early_s and not enc.stages:
                enc.stages.add("behind_early")
                text = self.say.pick(
                    "behind_early",
                    [
                        "{name} behind on a push lap, {gap}.",
                        "Car behind on a hot lap, {name}, {gap}.",
                        "Heads up, {name} pushing behind you, {gap}.",
                    ],
                    name=car.name,
                    gap=spoken_seconds(gap),
                )
                return RadioCall(f"traffic:{car.index}", Priority.STRATEGY, text, now, now + 3.0)
            return None

        if offset > 0 and mine == DriverStatus.FLYING_LAP and theirs in NOT_PUSHING:
            gap = gap_seconds(offset, me.speed_kph)
            closing = time_to_catch(offset, me.speed_kph, car.speed_kph) is not None
            if gap is None or gap > self.cfg.reset_s:
                enc.stages.clear()
                return None
            if closing and gap <= self.cfg.ahead_s and "ahead" not in enc.stages:
                enc.stages.add("ahead")
                lap_kind = "an out lap" if theirs == DriverStatus.OUT_LAP else "a slow lap"
                text = self.say.pick(
                    "ahead",
                    [
                        "Traffic ahead, {name} on {kind}, {gap}.",
                        "{name} ahead on {kind}, {gap}. They know you're coming.",
                        "Slow car ahead, {name}, {gap}.",
                    ],
                    name=car.name,
                    kind=lap_kind,
                    gap=spoken_seconds(gap),
                )
                return RadioCall(f"traffic:{car.index}", Priority.STRATEGY, text, now, now + 2.5)
            return None

        # Neither case applies (they're past, or nobody's pushing): the encounter is over.
        if offset > 0 and theirs == DriverStatus.FLYING_LAP:
            enc.stages.clear()
        return None
