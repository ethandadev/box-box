"""Session flow: the radio check when you head out, time remaining, the quali drop zone, session end."""

from __future__ import annotations

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity, lap_time
from box_box.state.model import LapCompleted
from box_box.telemetry import names
from box_box.telemetry.packets import DriverStatus

WEATHER_WORDS = {0: "clear skies", 1: "light cloud", 2: "overcast", 3: "light rain", 4: "heavy rain", 5: "stormy"}
Q1, Q2 = 5, 6
SQ1, SQ2 = 10, 11
KNOCKOUT = {Q1, Q2, SQ1, SQ2}  # segments where the slowest are eliminated
FINAL_QUALI = {7, 8, 9, 12, 13, 14}  # Q3, short, one-shot, SQ3, short sprint, one-shot sprint
ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth"}


class SessionDetector(Detector):
    name = "session"

    def reset(self) -> None:
        self._greeted = False
        self._time_called: set[int] = set()
        self._last_time_left: int | None = None
        self._in_drop_zone: bool | None = None
        self._last_chance_called = False
        self._ended = False
        self._prev_status: int | None = None
        self._runs = 0  # times the driver has left the garage this session
        self._in_garage: bool | None = None

    def on_rewind(self) -> None:
        self._last_time_left = None

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        s = world.session
        me = world.player
        if s is None or me is None or me.lap is None:
            return []
        out: list[RadioCall] = []
        out += self._greet(ctx)
        out += self._new_run(ctx)
        out += self._time_left(ctx)
        for change in ctx.changes:
            if isinstance(change, LapCompleted) and change.car_index == me.index and world.is_qualifying:
                out += self._drop_zone(ctx)
        out += self._last_chance(ctx)
        out += self._end(ctx)
        return out

    def _greet(self, ctx: Context) -> list[RadioCall]:
        if self._greeted:
            return []
        world = ctx.world
        s, me = world.session, world.player
        assert s and me and me.lap
        status = me.lap.driver_status
        common = {
            "me": ctx.me,
            "track": names.track_name(s.track_id),
            "session": world.session_name,
            "weather": WEATHER_WORDS.get(s.weather, "dry"),
            "track_temp": self._temp(ctx, s.track_temp),
        }
        if world.is_race:
            if world.lights_out or me.lap.lap_num > 1:
                self._greeted = True  # joined mid-race: don't pretend it's the start
                return []
            if not world.cars or me.lap.grid_position == 0:
                return []
            self._greeted = True
            key = "greet_sprint" if world.is_sprint else "greet_race"
            text = ctx.say(key, laps=world.total_laps, grid=me.lap.grid_position, **common)
        else:
            # Practice/quali: say hello as the driver leaves the garage (that's when engineers do it).
            if status == DriverStatus.IN_GARAGE:
                return []
            self._greeted = True
            if status == DriverStatus.FLYING_LAP:
                return []  # straight onto a hot lap (or we joined mid-run): no radio check mid-lap
            if world.is_time_trial:
                text = ctx.say("greet_time_trial", **common)
            elif world.is_qualifying:
                text = ctx.say("greet_quali", minutes=max(1, s.time_left_s // 60), **common)
            else:
                text = ctx.say("greet_practice", **common)
        return [ctx.call("greet", Priority.INFO, text, ttl=60, verbosity=Verbosity.QUIET)]

    def _new_run(self, ctx: Context) -> list[RadioCall]:
        """Each time the driver leaves the garage again in quali: the run, the clock, the out-lap job."""
        world = ctx.world
        me, s = world.player, world.session
        assert me and me.lap and s
        garage = me.lap.driver_status == DriverStatus.IN_GARAGE
        prev, self._in_garage = self._in_garage, garage
        if prev is None or not prev or garage:
            return []
        self._runs += 1
        if not world.is_qualifying or self._runs < 2 or s.time_left_s <= 0:
            return []
        minutes = max(1, s.time_left_s // 60)
        run = ORDINALS.get(self._runs, "next")
        text = ctx.say("quali_run", run=run, run_cap=run.capitalize(), minutes=minutes, me=ctx.me)
        return [ctx.call("greet", Priority.INFO, text, ttl=40)]

    @staticmethod
    def _temp(ctx: Context, celsius: int) -> str:
        if ctx.settings.engineer.units == "imperial":
            return f"{round(celsius * 9 / 5 + 32)} Fahrenheit"
        return f"{celsius}"

    def _time_left(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        s = world.session
        assert s
        if world.is_race or world.is_time_trial or not s.duration_s:
            return []
        left = s.time_left_s
        prev, self._last_time_left = self._last_time_left, left
        if prev is None:
            self._time_called |= {m for m in (10, 5, 2) if left <= m * 60}  # already past these on join
            return []
        for minutes in (10, 5, 2):
            mark = minutes * 60
            if prev > mark >= left and minutes not in self._time_called:
                self._time_called.add(minutes)
                verbosity = Verbosity.CHATTY if minutes == 10 else Verbosity.NORMAL
                return [
                    ctx.call(
                        "time_left",
                        Priority.INFO,
                        ctx.say("time_left", minutes=minutes, me=ctx.me),
                        ttl=30,
                        verbosity=verbosity,
                    )
                ]
        return []

    def _drop_zone(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        s, me = world.session, world.player
        assert s and me and me.lap
        if s.session_type not in KNOCKOUT:
            return []
        cutoff = self._cutoff(ctx)
        pos = me.lap.position
        danger = pos > cutoff
        prev, self._in_drop_zone = self._in_drop_zone, danger
        if danger:
            return [ctx.call("quali_pos", Priority.STRATEGY, ctx.say("quali_drop_zone", pos=pos, me=ctx.me), ttl=30)]
        if prev:
            return [ctx.call("quali_pos", Priority.INFO, ctx.say("quali_safe", pos=pos), ttl=30)]
        return []

    @staticmethod
    def _cutoff(ctx: Context) -> int:
        """Last position that goes through to the next quali segment."""
        s = ctx.world.session
        assert s
        if s.session_type in (Q2, SQ2):
            return 10
        running = len(ctx.world.running())
        return max(10, running - (6 if running >= 22 else 5))  # 22-car grids drop six, 20-car grids five

    def _last_chance(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        s, me = world.session, world.player
        assert s and me and me.lap
        status = me.lap.driver_status
        prev, self._prev_status = self._prev_status, status
        if self._last_chance_called or not world.is_qualifying:
            return []
        if prev != DriverStatus.FLYING_LAP and status == DriverStatus.FLYING_LAP:
            lap_s = (me.session_best_ms or 90_000) / 1000
            if 0 < s.time_left_s < lap_s + 5:
                self._last_chance_called = True
                return [ctx.call("last_chance", Priority.STRATEGY, ctx.say("quali_last_lap_chance", me=ctx.me), ttl=8)]
        return []

    def _end(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        if self._ended or world.is_race or world.is_time_trial:
            return []
        s, me = world.session, world.player
        assert s and me and me.lap
        ended = world.session_ended or any(e.code == "SEND" for e in ctx.events)
        if not ended:
            return []
        self._ended = True
        best = me.session_best_ms
        if world.is_qualifying:
            pos = me.lap.position
            key = "session_end_quali"
            if s.session_type in KNOCKOUT and pos > self._cutoff(ctx):
                key = "quali_out"
            elif pos == 1 and s.session_type in FINAL_QUALI:
                key = "quali_pole"
            return [ctx.call("session_end", Priority.INFO, ctx.say(key, pos=pos, me=ctx.me), ttl=120)]
        if best:
            text = ctx.say("session_end_practice", best=lap_time(best), pos=me.lap.position, me=ctx.me)
            return [ctx.call("session_end", Priority.INFO, text, ttl=120)]
        return []
