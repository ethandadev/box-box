"""Race engineering: the start, gaps and trends, overtakes, pit stops, the pit window, last lap, finish."""

from __future__ import annotations

import re
from dataclasses import dataclass

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity, gap, tenths
from box_box.engineer.strategy import tyre_offset
from box_box.state.model import CarState, LapCompleted, PitEntered, PitExited, Retired
from box_box.telemetry import names
from box_box.telemetry.packets import ResultStatus

POINTS_POSITIONS = 10


@dataclass
class _PendingMove:
    kind: str  # "gained" | "lost"
    other: int
    at: float


class RaceDetector(Detector):
    name = "race"

    def reset(self) -> None:
        self._lap_one_done = False
        self._gap_history: dict[int, list[tuple[int, float]]] = {}  # car index -> [(my lap, gap s)]
        self._last_gap_lap = 0
        self._pending: list[_PendingMove] = []
        self._within_second_of: int | None = None
        self._called_window_soon = 0
        self._called_box_lap = 0
        self._last_lap_called = False
        self._finished_called = False
        self._laps_to_go_called: set[int] = set()
        self._offset_said: dict[int, str] = {}  # car index -> last tyre-offset note given

    def on_rewind(self) -> None:
        # Keep the one-off calls (start, finish); drop pending moves and gap trends from the rewound future.
        self._pending.clear()
        self._gap_history.clear()

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        if not world.is_race:
            return []
        me = world.player
        if me is None or me.lap is None:
            return []
        calls: list[RadioCall] = []
        for change in ctx.changes:
            if isinstance(change, LapCompleted) and change.car_index == me.index:
                calls += self._on_my_lap(ctx, me, change.record.lap_num)
            elif isinstance(change, PitEntered) and change.car_index != me.index:
                calls += self._rival_pitted(ctx, me, world.cars[change.car_index])
            elif isinstance(change, PitExited) and change.car_index == me.index:
                calls += self._my_stop_done(ctx, me, change)
            elif isinstance(change, Retired) and change.car_index != me.index:
                calls += self._retired(ctx, me, world.cars[change.car_index])
        calls += self._overtakes(ctx, me)
        calls += self._fastest_lap(ctx, me)
        calls += self._finish(ctx, me)
        calls += self._within_second(ctx, me)
        return calls

    # -- laps ---------------------------------------------------------------------------------------

    def _on_my_lap(self, ctx: Context, me: CarState, completed_lap: int) -> list[RadioCall]:
        world = ctx.world
        assert me.lap
        calls: list[RadioCall] = []
        pos = me.lap.position
        total = world.total_laps

        if me.lap.result_status != ResultStatus.ACTIVE:
            return calls

        if not self._lap_one_done and completed_lap == 1:
            self._lap_one_done = True
            grid = me.lap.grid_position or pos
            diff = grid - pos
            if diff > 0:
                text = ctx.say("after_lap_one_gained", pos=pos, n=diff, me=ctx.me)
            elif diff < 0:
                text = ctx.say("after_lap_one_lost", pos=pos, n=-diff, me=ctx.me)
            else:
                text = ctx.say("after_lap_one_same", pos=pos, me=ctx.me)
            calls.append(ctx.call("lap1", Priority.INFO, text, ttl=25, category="positions"))
            self._record_gaps(ctx, me, completed_lap)
            return calls

        # The lap we're now on is the last one.
        if total and me.lap.lap_num == total and not self._last_lap_called:
            self._last_lap_called = True
            calls.append(ctx.call("last_lap", Priority.STRATEGY, ctx.say("last_lap", me=ctx.me), ttl=30))
            return calls

        to_go = (total - me.lap.lap_num + 1) if total else None
        if to_go in (10, 5) and to_go not in self._laps_to_go_called and total and total > to_go + 3:
            self._laps_to_go_called.add(to_go)
            calls.append(
                ctx.call(
                    "laps_to_go",
                    Priority.COLOUR,
                    ctx.say("laps_to_go", n=to_go, me=ctx.me),
                    ttl=30,
                    verbosity=Verbosity.CHATTY,
                )
            )

        calls += self._pit_window(ctx, me)
        calls += self._gaps(ctx, me, completed_lap)
        return calls

    def _record_gaps(
        self, ctx: Context, me: CarState, lap: int
    ) -> tuple[CarState | None, float | None, CarState | None, float | None]:
        world = ctx.world
        assert me.lap
        ahead = world.car_at_position(me.lap.position - 1)
        behind = world.car_at_position(me.lap.position + 1)
        g_ahead = world.race_gap_s(ahead, me) if ahead else None
        g_behind = world.race_gap_s(me, behind) if behind else None
        for car, g in ((ahead, g_ahead), (behind, g_behind)):
            if car is not None and g is not None and g >= 0:
                self._gap_history.setdefault(car.index, []).append((lap, g))
        return ahead, g_ahead, behind, g_behind

    def _gaps(self, ctx: Context, me: CarState, lap: int) -> list[RadioCall]:
        every = max(1, ctx.settings.callouts.gap_every_laps)
        prev_hist = {k: list(v) for k, v in self._gap_history.items()}
        ahead, g_ahead, behind, g_behind = self._record_gaps(ctx, me, lap)
        if lap - self._last_gap_lap < every:
            return []
        self._last_gap_lap = lap
        if me.in_pit_lane:
            return []

        def trend_text(car: CarState | None, g: float | None, is_ahead: bool) -> tuple[str, float | None]:
            if car is None or g is None:
                return "", None
            hist = [h for h in prev_hist.get(car.index, []) if h[0] < lap]
            if not hist:
                return "", None
            then_lap, then_gap = hist[-min(every, len(hist))]
            rate = (g - then_gap) / max(1, lap - then_lap)
            if abs(rate) < 0.05:
                return "holding steady", rate
            if is_ahead:
                return (f"you're closing, {tenths(rate)} a lap" if rate < 0 else f"dropping {tenths(rate)} a lap"), rate
            return (f"closing {tenths(rate)} a lap" if rate < 0 else f"you're pulling away, {tenths(rate)} a lap"), rate

        t_ahead, rate_ahead = trend_text(ahead, g_ahead, True)
        t_behind, rate_behind = trend_text(behind, g_behind, False)
        valid_ahead = ahead is not None and g_ahead is not None and 0 <= g_ahead < 60
        valid_behind = behind is not None and g_behind is not None and 0 <= g_behind < 60
        assert me.lap
        if me.lap.position == 1 and valid_behind:
            text = ctx.say(
                "gaps_leading",
                behind=ctx.name(behind, f"P{me.lap.position + 1}"),
                gap_behind=gap(g_behind),
                trend_behind=t_behind or "steady",
            )  # type: ignore[arg-type]
        elif valid_ahead and valid_behind:
            text = ctx.say(
                "gaps_both",
                ahead=ctx.name(ahead, f"P{me.lap.position - 1}"),
                gap_ahead=gap(g_ahead),  # type: ignore[arg-type]
                trend_ahead=t_ahead or "steady",
                behind=ctx.name(behind, f"P{me.lap.position + 1}"),
                gap_behind=gap(g_behind),  # type: ignore[arg-type]
            )
        elif valid_ahead:
            text = ctx.say(
                "gaps_ahead",
                ahead=ctx.name(ahead, f"P{me.lap.position - 1}"),
                gap_ahead=gap(g_ahead),
                trend_ahead=t_ahead or "steady",
            )  # type: ignore[union-attr,arg-type]
        elif valid_behind:
            text = ctx.say(
                "gaps_behind",
                behind=ctx.name(behind, f"P{me.lap.position + 1}"),
                gap_behind=gap(g_behind),
                trend_behind=t_behind or "steady",
            )  # type: ignore[union-attr,arg-type]
        else:
            return []
        text = text.replace(", .", ".").replace(" .", ".").replace("..", ".")
        text = re.sub(r"([.!?] )([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
        # Like a real pit wall: add the rival's tyre situation when it's new information.
        for car, ok in ((ahead, valid_ahead and me.lap.position > 1), (behind, valid_behind)):
            note = tyre_offset(me, car, behind=car is behind) if ok and car is not None else None
            if note and car is not None and self._offset_said.get(car.index) != note:
                self._offset_said[car.index] = note
                who = ctx.name(car, "He")
                text += " " + note.replace("He's", f"{who}'s", 1).replace("Same tyre,", f"{who} on the same tyre,", 1)
                break
        # 2026: whether the car right behind can use overtake mode on us.
        if (
            valid_behind
            and behind is not None
            and g_behind is not None
            and g_behind < 1.5
            and ctx.world.regulations_2026
            and behind.telemetry2 is not None
        ):
            if behind.telemetry2.overtake_available:
                text += " He has overtake."
                # Like Leclerc in Miami: the car behind using overtake on us may itself be under attack.
                third = ctx.world.car_at_position(me.lap.position + 2)
                g3 = ctx.world.race_gap_s(behind, third) if third else None
                if third is not None and g3 is not None and 0 <= g3 < 1.2:
                    text += f" {ctx.name(third, 'Another car')} right behind him."
            else:
                text += " He does not have overtake."
        return [ctx.call("gaps", Priority.INFO, text, ttl=20, category="gaps", verbosity=Verbosity.NORMAL)]

    def _within_second(self, ctx: Context, me: CarState) -> list[RadioCall]:
        assert me.lap
        ahead = ctx.world.car_at_position(me.lap.position - 1)
        if ahead is None or me.in_pit_lane:
            return []
        g = ctx.world.race_gap_s(ahead, me)
        if g is None:
            return []
        if g > 1.4:
            if self._within_second_of == ahead.index:
                self._within_second_of = None
            return []
        if 0 < g < 1.0 and self._within_second_of != ahead.index and me.lap.lap_num > 2:
            self._within_second_of = ahead.index
            return [
                ctx.call(
                    "within_second",
                    Priority.COLOUR,
                    ctx.say("drs_range", name=ctx.name(ahead, f"P{me.lap.position - 1}")),
                    ttl=8,
                    category="gaps",
                    verbosity=Verbosity.CHATTY,
                )
            ]
        return []

    # -- pit stops ----------------------------------------------------------------------------------

    def _pit_window(self, ctx: Context, me: CarState) -> list[RadioCall]:
        s = ctx.world.session
        assert me.lap and s
        ideal, latest = s.pit_window_ideal_lap, s.pit_window_latest_lap
        if not ideal or me.in_pit_lane:
            return []
        lap = me.lap.lap_num
        out: list[RadioCall] = []
        n = ideal - lap
        if n == 2 and self._called_window_soon != ideal:
            self._called_window_soon = ideal
            out.append(
                ctx.call(
                    "pit_window", Priority.INFO, ctx.say("pit_window_soon", n=n, ideal=ideal), ttl=30, category="pit"
                )
            )
        wear = me.tyre_wear_avg or 0.0
        if self._called_box_lap != ideal and (lap == ideal and wear >= 30 or latest and lap == latest):
            self._called_box_lap = ideal
            out.append(ctx.call("box", Priority.STRATEGY, ctx.say("box_now", me=ctx.me), ttl=40, category="pit"))
        return out

    def _rival_pitted(self, ctx: Context, me: CarState, car: CarState) -> list[RadioCall]:
        if me.lap is None or car.lap is None or me.in_pit_lane:
            return []
        world = ctx.world
        if car.lap.position == me.lap.position - 1:
            return [
                ctx.call(
                    f"pit:{car.index}",
                    Priority.STRATEGY,
                    ctx.say("car_ahead_pitted", name=ctx.name(car, "The car ahead")),
                    ttl=15,
                    category="pit",
                )
            ]
        if car.lap.position == me.lap.position + 1:
            g = world.race_gap_s(me, car)
            if g is not None and g < 4.0:
                return [
                    ctx.call(
                        f"pit:{car.index}",
                        Priority.STRATEGY,
                        ctx.say("car_behind_pitted", name=ctx.name(car, "The car")),
                        ttl=15,
                        category="pit",
                    )
                ]
        return []

    def _my_stop_done(self, ctx: Context, me: CarState, change: PitExited) -> list[RadioCall]:
        if change.stationary_ms <= 0 or me.lap is None:
            return []
        secs = f"{change.stationary_ms / 1000:.1f}"
        text = ctx.say("pit_stop_done", seconds=secs, pos=me.lap.position)
        tyre = names.compound_name(change.new_compound) if change.new_compound else "unknown tyres"
        if tyre != "unknown tyres":
            text += " " + ctx.say("new_tyres", tyre=tyre, me=ctx.me)
        return [ctx.call("pit_done", Priority.INFO, text, ttl=20, category="pit", verbosity=Verbosity.NORMAL)]

    # -- overtakes ----------------------------------------------------------------------------------

    def _overtakes(self, ctx: Context, me: CarState) -> list[RadioCall]:
        assert me.lap
        for e in ctx.events:
            if e.code != "OVTK" or me.lap.lap_num < 2:
                continue
            if e.vehicle_idx == me.index and e.other_vehicle_idx is not None:
                self._add_move(ctx, "gained", e.other_vehicle_idx)
            elif e.other_vehicle_idx == me.index and e.vehicle_idx is not None:
                self._add_move(ctx, "lost", e.vehicle_idx)
        out: list[RadioCall] = []
        keep: list[_PendingMove] = []
        for move in self._pending:
            if ctx.now - move.at < 3.0:
                keep.append(move)
                continue
            other = ctx.world.cars[move.other] if 0 <= move.other < len(ctx.world.cars) else None
            pos = me.lap.position
            if move.kind == "gained":
                text = ctx.say("gained_position", pos=pos, me=ctx.me)
                out.append(
                    ctx.call("move", Priority.INFO, text, ttl=8, category="positions", verbosity=Verbosity.NORMAL)
                )
            elif other is not None:
                text = ctx.say("lost_position", name=other.name, pos=pos, old=pos - 1)
                out.append(
                    ctx.call("move", Priority.INFO, text, ttl=8, category="positions", verbosity=Verbosity.NORMAL)
                )
        self._pending = keep
        return out

    def _add_move(self, ctx: Context, kind: str, other: int) -> None:
        # A pass straight back within 3 s cancels both: nothing changed.
        for m in self._pending:
            if m.other == other and m.kind != kind:
                self._pending.remove(m)
                return
        self._pending.append(_PendingMove(kind, other, ctx.now))

    def _fastest_lap(self, ctx: Context, me: CarState) -> list[RadioCall]:
        for e in ctx.events:
            if e.code == "FTLP" and e.vehicle_idx == me.index:
                text = ctx.say("fastest_lap_me", me=ctx.me)
                return [
                    ctx.call(
                        "fastest", Priority.COLOUR, text, ttl=15, category="lap_feedback", verbosity=Verbosity.NORMAL
                    )
                ]
        return []

    def _retired(self, ctx: Context, me: CarState, car: CarState) -> list[RadioCall]:
        if me.lap is None or car.lap is None:
            return []
        if abs(car.lap.position - me.lap.position) > 3:
            return []
        return [
            ctx.call(
                f"ret:{car.index}",
                Priority.INFO,
                ctx.say("retirement_near", name=car.name),
                ttl=20,
                verbosity=Verbosity.NORMAL,
            )
        ]

    # -- finish -------------------------------------------------------------------------------------

    def _finish(self, ctx: Context, me: CarState) -> list[RadioCall]:
        assert me.lap
        if self._finished_called:
            return []
        if me.lap.result_status != ResultStatus.FINISHED:
            return []
        self._finished_called = True
        pos = me.lap.position
        key = (
            "finish_win"
            if pos == 1
            else "finish_podium"
            if pos <= 3
            else "finish_points"
            if pos <= POINTS_POSITIONS
            else "finish_no_points"
        )
        text = ctx.say(key, pos=pos, me=ctx.me)
        grid = me.lap.grid_position
        if grid and pos != 1:
            if grid - pos >= 2:
                text += " " + ctx.say("finish_gained", n=grid - pos)
            elif pos - grid >= 3 and key == "finish_no_points":
                text += " " + ctx.say("finish_lost")
            elif pos - grid >= 2:
                text += " " + ctx.say("finish_damage_limitation")
        return [ctx.call("finish", Priority.STRATEGY, text, ttl=60)]
