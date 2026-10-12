"""Traffic management in practice and qualifying.

- A car on a push lap closing on you while you're on an out/in lap: an early heads-up, then "let them by".
- You on a push lap closing on a slow car: "traffic ahead".
- A car leaving the pit lane just ahead of you.

Each encounter is called at most once per stage and resets once the car is past or well back, so the
engineer never nags. Cars that are already alongside when they first become relevant (you can see
them) get no call: tuned on a real practice session where an in-lap car ran wheel to wheel for 40 s.

Gaps in seconds use the lap's average speed, not the speed this instant: braking for a hairpin made a
6 s gap read 20 s, which looked like the encounter was over, and the same car was called five times.
With several cars about, the heads-ups are spaced out rather than fired one after another.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, spoken_seconds
from box_box.state.model import CarState
from box_box.state.track import gap_seconds, road_offset, time_to_catch
from box_box.telemetry.packets import DriverStatus

NOT_PUSHING = frozenset({DriverStatus.OUT_LAP, DriverStatus.IN_LAP, DriverStatus.ON_TRACK})


@dataclass
class TrafficConfig:
    behind_early_s: float = 8.0  # first heads-up for a pushing car behind
    behind_close_s: float = 3.0  # "let them by"
    ahead_s: float = 6.0  # slow car ahead while you're on a push lap
    min_first_ahead_s: float = 1.5  # already this close when it becomes relevant: you can see it, stay quiet
    pit_exit_s: float = 6.0
    reset_s: float = 15.0  # encounter over once the gap grows past this
    forget_s: float = 90.0  # ...and nothing has been said about that car for this long
    spacing_s: float = 20.0  # between heads-ups about different cars of the same kind
    min_speed_kph: float = 60.0  # ignore cars crawling out of the garage or spun


@dataclass
class _Encounter:
    stages: set[str] = field(default_factory=set)
    said_at: float = -1e9


class TrafficDetector(Detector):
    name = "traffic"

    def __init__(self, config: TrafficConfig | None = None) -> None:
        self.cfg = config or TrafficConfig()
        super().__init__()

    def reset(self) -> None:
        self._encounters: dict[int, _Encounter] = {}
        self._last_kind: dict[str, float] = {}  # "behind" / "ahead" / "pit_exit" -> last heads-up time

    def _gap_s(self, ctx: Context, gap_m: float, instant_kph: float) -> float | None:
        """Seconds between two cars at a typical lap pace (falls back to the speed now)."""
        ref = ctx.world.best_lap_overall()
        if ref is not None and ref[1] > 0 and ctx.world.track_length > 0:
            avg_kph = ctx.world.track_length / (ref[1] / 1000) * 3.6
            return gap_seconds(gap_m, avg_kph)
        return gap_seconds(gap_m, instant_kph)

    def _spaced(self, ctx: Context, kind: str) -> bool:
        """True (and arms the timer) unless we gave this kind of heads-up a moment ago."""
        if ctx.now - self._last_kind.get(kind, -1e9) < self.cfg.spacing_s:
            return False
        self._last_kind[kind] = ctx.now
        return True

    def _over(self, ctx: Context, enc: _Encounter) -> None:
        if ctx.now - enc.said_at > self.cfg.forget_s:
            enc.stages.clear()

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        if world.is_race or world.track_length <= 0:
            return []
        me = world.player
        if me is None or not me.on_track or me.lap is None:
            return []

        calls: list[RadioCall] = []
        for car in world.others():
            if car.lap is None:
                continue
            if self._leaving_pits(car):
                call = self._check_pit_exit(ctx, me, car)
            elif not car.on_track:
                self._encounters.pop(car.index, None)
                continue
            elif car.speed_kph < self.cfg.min_speed_kph:
                continue  # crawling (a hairpin on an in lap, a spin): skip it, but remember what we said
            else:
                call = self._check(ctx, me, car)
            if call:
                calls.append(call)
        return calls

    @staticmethod
    def _leaving_pits(car: CarState) -> bool:
        return bool(
            car.lap and car.lap.pit_status == 1 and car.lap.driver_status == DriverStatus.OUT_LAP and car.speed_kph > 30
        )

    def _check_pit_exit(self, ctx: Context, me: CarState, car: CarState) -> RadioCall | None:
        assert me.lap and car.lap
        enc = self._encounters.setdefault(car.index, _Encounter())
        if "pit_exit" in enc.stages or me.driver_status != DriverStatus.FLYING_LAP:
            return None
        offset = road_offset(me.lap.lap_distance, car.lap.lap_distance, ctx.world.track_length)
        if offset <= 0:
            return None
        gap = self._gap_s(ctx, offset, me.speed_kph)
        if gap is None or gap > self.cfg.pit_exit_s:
            return None
        enc.stages |= {"pit_exit", "ahead"}  # one call covers the whole encounter
        enc.said_at = ctx.now
        if not self._spaced(ctx, "pit_exit"):
            return None
        text = ctx.say("pit_exit_ahead", name=car.name)
        return ctx.call(f"traffic:{car.index}", Priority.STRATEGY, text, ttl=3.0, category="traffic")

    def _check(self, ctx: Context, me: CarState, car: CarState) -> RadioCall | None:
        assert me.lap and car.lap
        offset = road_offset(me.lap.lap_distance, car.lap.lap_distance, ctx.world.track_length)
        mine, theirs = me.driver_status, car.driver_status
        enc = self._encounters.setdefault(car.index, _Encounter())

        if offset < 0 and theirs == DriverStatus.FLYING_LAP and mine in NOT_PUSHING:
            gap_m = -offset
            gap = self._gap_s(ctx, gap_m, car.speed_kph)
            closing = time_to_catch(gap_m, car.speed_kph, me.speed_kph) is not None
            if gap is None or gap > self.cfg.reset_s:
                self._over(ctx, enc)
                return None
            if not closing:
                return None
            if gap <= self.cfg.behind_close_s and "behind_close" not in enc.stages:
                enc.stages |= {"behind_close", "behind_early"}
                enc.said_at = ctx.now
                text = ctx.say("behind_close", name=car.name)
                return ctx.call(f"traffic:{car.index}", Priority.SAFETY, text, ttl=2.0, category="traffic")
            if gap <= self.cfg.behind_early_s and not enc.stages:
                enc.stages.add("behind_early")
                enc.said_at = ctx.now
                if not self._spaced(ctx, "behind"):
                    return None  # another car's heads-up just went out; the close call still comes
                text = ctx.say("behind_early", name=car.name, gap=spoken_seconds(gap))
                return ctx.call(f"traffic:{car.index}", Priority.STRATEGY, text, ttl=3.0, category="traffic")
            return None

        if offset > 0 and mine == DriverStatus.FLYING_LAP and theirs in NOT_PUSHING:
            gap = self._gap_s(ctx, offset, me.speed_kph)
            closing = time_to_catch(offset, me.speed_kph, car.speed_kph) is not None
            if gap is None or gap > self.cfg.reset_s:
                self._over(ctx, enc)
                return None
            if "ahead" in enc.stages:
                return None
            if gap < self.cfg.min_first_ahead_s:
                enc.stages.add("ahead")  # already right there: the driver can see them
                return None
            if closing and gap <= self.cfg.ahead_s:
                enc.stages.add("ahead")
                enc.said_at = ctx.now
                if not self._spaced(ctx, "ahead"):
                    return None
                kind = (
                    "an out lap"
                    if theirs == DriverStatus.OUT_LAP
                    else "an in lap"
                    if theirs == DriverStatus.IN_LAP
                    else "a slow lap"
                )
                text = ctx.say("ahead_slow", name=car.name, kind=kind, gap=spoken_seconds(gap))
                return ctx.call(f"traffic:{car.index}", Priority.STRATEGY, text, ttl=2.5, category="traffic")
            return None

        # Neither case applies (they're past, or nobody's pushing): the encounter is over.
        if offset > 0 and theirs == DriverStatus.FLYING_LAP:
            enc.stages.clear()  # the pushing car is past us: a new encounter next time round
        return None
