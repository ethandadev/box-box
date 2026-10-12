"""Lap and sector feedback in practice, qualifying and time trial; long-run summaries in practice."""

from __future__ import annotations

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity, fine_delta, lap_time, tenths
from box_box.state.model import CarState, LapCompleted, LapRecord, PitEntered
from box_box.telemetry.packets import DriverStatus


class LapFeedbackDetector(Detector):
    name = "laps"

    def reset(self) -> None:
        self._sector_seen: tuple[int, int] | None = None  # (lap, sector) last seen
        self._stint_laps: list[LapRecord] = []

    def on_rewind(self) -> None:
        self._sector_seen = None

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        me = world.player
        if me is None or me.lap is None or world.is_race:
            return []
        calls: list[RadioCall] = []
        for change in ctx.changes:
            if isinstance(change, LapCompleted) and change.car_index == me.index:
                calls += self._lap(ctx, me, change.record)
            elif isinstance(change, PitEntered) and change.car_index == me.index:
                calls += self._run_summary(ctx)
        calls += self._purple_sector(ctx, me)
        return calls

    def _lap(self, ctx: Context, me: CarState, rec: LapRecord) -> list[RadioCall]:
        if rec.pit_lap or rec.lap_ms <= 0:
            return []  # out/in laps aren't representative
        if not rec.valid:
            return []  # the deletion was already called live
        self._stint_laps.append(rec)
        earlier = [r for r in me.valid_laps() if r is not rec and r.lap_num != rec.lap_num]
        best_before = min((r.lap_ms for r in earlier), default=None)
        if me.history and me.history.best_lap_ms and me.history.best_lap_ms != rec.lap_ms:
            hb = me.history.best_lap_ms
            best_before = hb if best_before is None else min(best_before, hb)

        t = lap_time(rec.lap_ms)
        world = ctx.world
        overall = world.best_lap_overall()
        others_have_times = any(c.session_best_ms for c in world.others())

        if best_before is None or rec.lap_ms < best_before:
            if overall and overall[0].index == me.index and others_have_times:
                key = "lap_provisional_pole" if world.is_qualifying else "lap_pb_session_best"
                text = ctx.say(key, time=t, me=ctx.me)
            else:
                text = ctx.say("lap_pb", time=t, me=ctx.me)
                text += self._position_suffix(ctx, me)
            return [ctx.call("lap", Priority.INFO, text, ttl=25, category="lap_feedback")]

        delta = (rec.lap_ms - best_before) / 1000.0
        if delta < 0.15:
            text = ctx.say("lap_close", time=t, delta=fine_delta(delta))
        else:
            sector = self._worst_sector(me, rec)
            text = ctx.say("lap_slower", time=t, delta=tenths(delta), sector=sector)
        return [ctx.call("lap", Priority.INFO, text, ttl=25, category="lap_feedback", verbosity=Verbosity.NORMAL)]

    @staticmethod
    def _position_suffix(ctx: Context, me: CarState) -> str:
        world = ctx.world
        if not (world.is_qualifying or world.is_practice) or me.lap is None:
            return ""
        overall = world.best_lap_overall()
        mine = me.session_best_ms
        if not overall or not mine or overall[0].index == me.index:
            return ""
        delta = (mine - overall[1]) / 1000.0
        if world.is_qualifying and me.lap.position == 2 and delta < 0.1:
            return " " + ctx.say("lap_so_close_to_pole", delta=fine_delta(delta), name=overall[0].name, me=ctx.me)
        return " " + ctx.say("lap_position", pos=me.lap.position, delta=fine_delta(delta), name=overall[0].name)

    @staticmethod
    def _worst_sector(me: CarState, rec: LapRecord) -> int:
        laps = [
            r for r in me.valid_laps() if r.lap_num != rec.lap_num and r.sector1_ms and r.sector2_ms and r.sector3_ms
        ]
        if not laps:
            return 1
        best = [min(r.sector1_ms for r in laps), min(r.sector2_ms for r in laps), min(r.sector3_ms for r in laps)]
        mine = [rec.sector1_ms, rec.sector2_ms, rec.sector3_ms]
        losses = [m - b if m and b else 0 for m, b in zip(mine, best, strict=True)]
        return losses.index(max(losses)) + 1

    def _purple_sector(self, ctx: Context, me: CarState) -> list[RadioCall]:
        lap = me.lap
        assert lap
        key = (lap.lap_num, lap.sector)
        prev, self._sector_seen = self._sector_seen, key
        if prev is None or prev == key or me.driver_status != DriverStatus.FLYING_LAP or lap.lap_invalid:
            return []
        if prev[0] != lap.lap_num or lap.sector == 0:
            return []
        done = lap.sector  # sector index just completed is sector-1 (0-based) => 1-based == lap.sector
        my_time = lap.sector1_ms if done == 1 else lap.sector2_ms if done == 2 else 0
        if not my_time:
            return []
        best = ctx.world.best_sectors_overall()[done - 1]
        if best is None or my_time < best:
            text = ctx.say("purple_sector", sector=done)
            return [
                ctx.call("sector", Priority.COLOUR, text, ttl=6, category="lap_feedback", verbosity=Verbosity.CHATTY)
            ]
        return []

    def _run_summary(self, ctx: Context) -> list[RadioCall]:
        laps, self._stint_laps = self._stint_laps, []
        if len(laps) < 4 or not ctx.world.is_practice:
            return []
        times = [r.lap_ms / 1000 for r in laps]
        avg = sum(times) / len(times)
        n = len(times)
        xs = range(n)
        mx = (n - 1) / 2
        slope = sum((x - mx) * (t - avg) for x, t in zip(xs, times, strict=True)) / sum((x - mx) ** 2 for x in xs)
        text = ctx.say("long_run_summary", avg=lap_time(round(avg * 1000)), deg=tenths(max(0.0, slope)))
        return [
            ctx.call("run_summary", Priority.INFO, text, ttl=60, category="lap_feedback", verbosity=Verbosity.NORMAL)
        ]
