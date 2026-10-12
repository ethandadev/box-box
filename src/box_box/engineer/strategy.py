"""The strategist on the pit wall: what a race engineer works out while the driver drives.

- Before the start: who's on which tyre ("Most of the field on mediums. Sainz P7 on hards.").
- During a stint: is the tyre holding up to plan ("These tyres are robust, happy to push on them"),
  should we extend ("Plan A plus three"), or are we heading for plan B.
- The safety-car window: tell the driver we're in it, and box when the safety car comes.
- Rival intel: cars yet to stop, long stints, tyre offsets.
- Asking for feedback like a real engineer ("How's the balance?"); the answer is remembered.
"""

from __future__ import annotations

from collections import Counter

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity
from box_box.state.model import CarState, LapCompleted
from box_box.telemetry import names

TYRE_CLIFF_PCT = 70.0  # average wear where pace falls away
MIN_GRID_CARS = 6

NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def count_word(n: int) -> str:
    return NUMBERS[n] if 0 <= n < len(NUMBERS) else str(n)


def wear_rate(me: CarState) -> float | None:
    from box_box.brain.tools import wear_rate_per_lap  # local import: brain depends on engineer, not back

    return wear_rate_per_lap(me)


def tyre_life_left(me: CarState) -> float | None:
    """Laps until the tyres reach the cliff at the current wear rate."""
    wear, rate = me.tyre_wear_avg, wear_rate(me)
    if wear is None or not rate or rate <= 0:
        return None
    return max(0.0, (TYRE_CLIFF_PCT - wear) / rate)


SOFTNESS = {"softs": 0, "super softs": 0, "mediums": 1, "hards": 2}


def tyre_offset(me: CarState, car: CarState, behind: bool = False) -> str | None:
    """'He's on hards, six laps older.' when the rival's tyres differ enough to matter, else None.

    For the car behind on a softer tyre, add what that means: he'll have higher deg."""
    if me.status is None or car.status is None:
        return None
    mine, theirs = names.compound_name(me.status.visual_compound), names.compound_name(car.status.visual_compound)
    if theirs == "unknown tyres":
        return None
    diff = car.status.tyre_age_laps - me.status.tyre_age_laps
    age = f"{count_word(abs(diff))} laps {'older' if diff > 0 else 'newer'}" if abs(diff) >= 4 else ""
    if theirs != mine:
        note = f"He's on {theirs}" + (f", {age}." if age else ".")
        if behind and SOFTNESS.get(theirs, 1) < SOFTNESS.get(mine, 1):
            note += " He'll have higher deg."
        return note
    if age:
        return f"Same tyre, {age}."
    return None


class StrategyDetector(Detector):
    name = "strategy"

    def reset(self) -> None:
        self._grid_brief = False
        self._verdicts: set[tuple[int, str]] = set()  # (stint, verdict) already given
        self._sc_window_stint: int | None = None  # stint we told the driver is in the SC window
        self._sc_status = 0
        self._sc_boxed_stint: int | None = None
        self._yet_to_stop: set[int] = set()
        self._asked: set[str] = set()
        self._offset_attack: set[int] = set()
        self._rhythm_lap = -10
        self._nothing_to_prove = False
        self._sc_temps_called = False

    def on_rewind(self) -> None:
        self._verdicts.clear()
        self._sc_boxed_stint = None

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        me = world.player
        if me is None or me.lap is None or world.session is None:
            return []
        out: list[RadioCall] = []
        if world.is_race:
            out += self._grid_tyres(ctx, me)
            completed = any(isinstance(c, LapCompleted) and c.car_index == me.index for c in ctx.changes)
            if completed and world.lights_out and not me.in_pit_lane:
                out += self._tyre_plan(ctx, me)
                out += self._sc_window(ctx, me)
                out += self._rivals_yet_to_stop(ctx, me)
                out += self._tyre_offset_attack(ctx, me)
                out += self._rhythm(ctx, me)
                out += self._nothing_to_prove_call(ctx, me)
                out += self._ask_tyre_feedback(ctx, me)
            out += self._safety_car_box(ctx, me)
            out += self._safety_car_tyre_temps(ctx, me)
            if completed and world.lights_out:
                out += self._ask_conditions(ctx, me)
        elif world.is_practice:
            if any(isinstance(c, LapCompleted) and c.car_index == me.index for c in ctx.changes):
                out += self._ask_balance(ctx, me)
        return out

    # ---- before the start ------------------------------------------------------------------------

    def _grid_tyres(self, ctx: Context, me: CarState) -> list[RadioCall]:
        world = ctx.world
        if self._grid_brief:
            return []
        if world.lights_out or me.lap is None or me.lap.lap_num > 1:
            self._grid_brief = True  # joined late: the start has gone
            return []
        field = [c for c in world.running() if c.status and c.lap and c.lap.grid_position]
        known = [c for c in field if names.compound_name(c.status.visual_compound) != "unknown tyres"]  # type: ignore[union-attr]
        if len(known) < MIN_GRID_CARS or len(known) < len(field):
            return []
        self._grid_brief = True
        tyre = {c.index: names.compound_name(c.status.visual_compound) for c in known}  # type: ignore[union-attr]
        counts = Counter(tyre.values())
        common, n_common = counts.most_common(1)[0]
        mine = tyre.get(me.index)
        if n_common == len(known):
            text = f"Whole field on {common}, same as us."
        elif n_common * 2 <= len(known):
            split = ", ".join(f"{count_word(n)} on {compound}" for compound, n in counts.most_common())
            text = f"Mixed strategies: {split}." + (f" We're on {mine}." if mine else "")
        else:
            parts = [f"Most of the field on {common}."]
            for compound, _n in counts.most_common():
                if compound == common:
                    continue
                cars = sorted(
                    (c for c in known if tyre[c.index] == compound and c.index != me.index),
                    key=lambda c: c.lap.grid_position,  # type: ignore[union-attr]
                )
                if not cars:
                    continue
                if len(cars) <= 3:
                    who = ", ".join(f"{c.name} P{c.lap.grid_position}" for c in cars)  # type: ignore[union-attr]
                    parts.append(f"On {compound}: {who}.")
                else:
                    parts.append(f"{count_word(len(cars)).capitalize()} cars on {compound}.")
            if mine:
                parts.append(f"We're on {mine}.")
            text = " ".join(parts)
        return [ctx.call("grid_tyres", Priority.INFO, text, ttl=120, category="pit", verbosity=Verbosity.NORMAL)]

    # ---- the stint -------------------------------------------------------------------------------

    def _target_laps(self, ctx: Context, me: CarState) -> tuple[int | None, bool]:
        """Laps this set of tyres has to do, and whether that's to a planned stop (vs. the finish)."""
        world = ctx.world
        s = world.session
        assert s and me.lap
        lap = me.lap.lap_num
        remaining = world.laps_remaining
        if s.pit_window_ideal_lap and me.pit_stops == 0 and lap <= s.pit_window_ideal_lap:
            return s.pit_window_ideal_lap - lap, True
        return remaining, False

    def _say_once(self, ctx: Context, stint: int, verdict: str) -> bool:
        if (stint, verdict) in self._verdicts:
            return False
        self._verdicts.add((stint, verdict))
        return True

    def _tyre_plan(self, ctx: Context, me: CarState) -> list[RadioCall]:
        if me.status is None or me.lap is None:
            return []
        age = me.status.tyre_age_laps
        life = tyre_life_left(me)
        target, to_stop = self._target_laps(ctx, me)
        if life is None or target is None or age < 4 or target < 1:
            return []
        stint = me.pit_stops
        s = ctx.world.session
        assert s
        lap = me.lap.lap_num
        # Running out before the planned stop or the flag: plan B.
        if target >= 3 and life < target - 2 and self._say_once(ctx, stint, "plan_b"):
            text = ctx.say("plan_b", n=max(1, round(life)), me=ctx.me)
            return [ctx.call("tyre_plan", Priority.STRATEGY, text, ttl=40, category="pit")]
        # The planned stop is close but the tyres have plenty left: extend.
        latest = s.pit_window_latest_lap or 0
        if to_stop and 0 < target <= 2 and latest > lap + target:
            extra = min(latest - (lap + target), int(life - target) - 2)
            projected = (me.tyre_wear_avg or 0) + (wear_rate(me) or 0) * target
            if extra >= 2 and projected < 30 and self._say_once(ctx, stint, "extend"):
                text = ctx.say("plan_extend", n=count_word(extra), me=ctx.me)
                return [ctx.call("tyre_plan", Priority.STRATEGY, text, ttl=40, category="pit")]
        # Comfortably inside the plan: let the driver lean on them.
        if target >= 3 and life >= target + 4 and 4 <= age <= 14 and self._say_once(ctx, stint, "robust"):
            if to_stop and s.pit_window_ideal_lap and ctx.say.chance(0.5):
                text = ctx.say("plan_a_target", lap=s.pit_window_ideal_lap, me=ctx.me)
            elif not to_stop and me.pit_stops > 0:
                text = ctx.say("tyres_to_the_end", me=ctx.me)
            else:
                text = ctx.say("tyres_robust", me=ctx.me)
            return [ctx.call("tyre_plan", Priority.INFO, text, ttl=40, category="tyres", verbosity=Verbosity.NORMAL)]
        return []

    # ---- safety car ------------------------------------------------------------------------------

    def _in_sc_window(self, ctx: Context, me: CarState) -> bool:
        world = ctx.world
        s = world.session
        assert s and me.lap
        remaining = world.laps_remaining or 0
        if remaining < 8 or me.status is None:
            return False
        if s.pit_window_ideal_lap and me.pit_stops == 0:
            return me.lap.lap_num >= s.pit_window_ideal_lap - 6
        # No game window (or already stopped): a cheap stop is worth it on worn tyres.
        return me.status.tyre_age_laps >= 10 and (me.tyre_wear_avg or 0) >= 25

    def _sc_window(self, ctx: Context, me: CarState) -> list[RadioCall]:
        stint = me.pit_stops
        if self._sc_window_stint == stint or not self._in_sc_window(ctx, me):
            return []
        self._sc_window_stint = stint
        text = ctx.say("sc_window", me=ctx.me)
        return [ctx.call("sc_window", Priority.INFO, text, ttl=40, category="pit", verbosity=Verbosity.NORMAL)]

    def _safety_car_box(self, ctx: Context, me: CarState) -> list[RadioCall]:
        s = ctx.world.session
        assert s
        prev, self._sc_status = self._sc_status, s.safety_car_status
        if s.safety_car_status not in (1, 2) or prev == s.safety_car_status:
            return []
        stint = me.pit_stops
        if me.in_pit_lane or self._sc_boxed_stint == stint or not ctx.world.lights_out:
            return []
        if self._sc_window_stint != stint and not self._in_sc_window(ctx, me):
            return []
        self._sc_boxed_stint = stint
        key = "box_under_sc" if s.safety_car_status == 1 else "box_under_vsc"
        return [ctx.call("box", Priority.STRATEGY, ctx.say(key, me=ctx.me), ttl=25, category="pit")]

    def _safety_car_tyre_temps(self, ctx: Context, me: CarState) -> list[RadioCall]:
        """Behind the safety car the tyres cool fast: tell the driver to weave and keep temperature in."""
        s = ctx.world.session
        assert s
        if s.safety_car_status not in (1, 2):
            self._sc_temps_called = False
            return []
        t = me.telemetry
        if self._sc_temps_called or t is None or me.in_pit_lane or not me.on_track:
            return []
        avg = sum(t.tyre_inner_temps) / 4
        if not 0 < avg < 78:
            return []
        self._sc_temps_called = True
        text = ctx.say("sc_cold_tyres", me=ctx.me)
        return [ctx.call("sc_temps", Priority.INFO, text, ttl=30, category="tyres", verbosity=Verbosity.NORMAL)]

    # ---- rivals ----------------------------------------------------------------------------------

    def _rivals_yet_to_stop(self, ctx: Context, me: CarState) -> list[RadioCall]:
        world = ctx.world
        if me.pit_stops == 0 or me.status is None or me.lap is None:
            return []
        remaining = world.laps_remaining or 0
        for pos in (me.lap.position - 1, me.lap.position - 2):
            car = world.car_at_position(pos)
            if car is None or car.status is None or car.pit_stops > 0 or car.index in self._yet_to_stop:
                continue
            age = car.status.tyre_age_laps
            if age < me.status.tyre_age_laps + 8:
                continue
            self._yet_to_stop.add(car.index)
            gap_s = world.race_gap_s(car, me)
            key = "rival_going_long" if remaining and remaining <= age * 0.6 else "rival_yet_to_stop"
            text = ctx.say(
                key,
                name=car.name,
                age=age,
                tyre=names.compound_name(car.status.visual_compound),
                gap=f"{gap_s:.1f} up the road" if gap_s is not None and gap_s < 10 else "up the road",
            )
            return [ctx.call(f"rival:{car.index}", Priority.INFO, text, ttl=40, category="pit")]
        return []

    def _tyre_offset_attack(self, ctx: Context, me: CarState) -> list[RadioCall]:
        """We've stopped, the car ahead stopped long ago: fresher tyres, so the end of the race is ours."""
        world = ctx.world
        if me.status is None or me.lap is None or me.pit_stops == 0:
            return []
        if (world.laps_remaining or 0) < 4:
            return []
        car = world.car_at_position(me.lap.position - 1)
        if car is None or car.status is None or car.pit_stops == 0 or car.index in self._offset_attack:
            return []  # cars yet to stop are covered by _rivals_yet_to_stop
        n = car.status.tyre_age_laps - me.status.tyre_age_laps
        gap_s = world.race_gap_s(car, me)
        if n < 8 or gap_s is None or gap_s > 12:
            return []
        self._offset_attack.add(car.index)
        text = ctx.say("tyre_offset_attack", name=car.name, n=count_word(n), me=ctx.me)
        return [ctx.call(f"rival:{car.index}", Priority.INFO, text, ttl=30, category="pit", verbosity=Verbosity.NORMAL)]

    def _rhythm(self, ctx: Context, me: CarState) -> list[RadioCall]:
        """A scruffy lap (a mistake, not traffic in the pits or a safety car): steady the driver."""
        world = ctx.world
        s = world.session
        assert s and me.lap
        laps = [r for r in me.laps if r.lap_ms > 0 and not r.pit_lap]
        if len(laps) < 5 or s.safety_car_status != 0 or me.lap.lap_num - self._rhythm_lap < 5:
            return []
        last, recent = laps[-1], sorted(r.lap_ms for r in laps[-6:-1])
        median = recent[len(recent) // 2]
        if last.lap_ms - median < 1500 or last.lap_ms - median > 15000:  # >15 s: an incident, not rhythm
            return []
        self._rhythm_lap = me.lap.lap_num
        return [ctx.call("rhythm", Priority.COLOUR, ctx.say("rhythm", me=ctx.me), ttl=20, verbosity=Verbosity.NORMAL)]

    def _nothing_to_prove_call(self, ctx: Context, me: CarState) -> list[RadioCall]:
        world = ctx.world
        assert me.lap
        remaining = world.laps_remaining
        if self._nothing_to_prove or me.lap.position != 1 or remaining is None or not 2 <= remaining <= 10:
            return []
        behind = world.car_at_position(2)
        g = world.race_gap_s(me, behind) if behind else None
        if g is None or g < 5:
            return []
        self._nothing_to_prove = True
        text = ctx.say("nothing_to_prove", gap=f"{round(g)} seconds", me=ctx.me)
        return [ctx.call("lead", Priority.INFO, text, ttl=30, verbosity=Verbosity.QUIET)]

    # ---- asking the driver -----------------------------------------------------------------------

    def _ask_tyre_feedback(self, ctx: Context, me: CarState) -> list[RadioCall]:
        if me.status is None or "tyres" in self._asked or me.status.tyre_age_laps < 6:
            return []
        if (ctx.world.laps_remaining or 0) < 6:
            return []
        self._asked.add("tyres")
        text = ctx.say("ask_tyres", me=ctx.me)
        return [ctx.call("ask:tyres", Priority.COLOUR, text, ttl=40, verbosity=Verbosity.NORMAL)]

    def _ask_conditions(self, ctx: Context, me: CarState) -> list[RadioCall]:
        s = ctx.world.session
        assert s and me.lap
        if "conditions" in self._asked or s.weather < 3 or me.lap.lap_num < 3:
            return []
        self._asked.add("conditions")
        text = ctx.say("ask_conditions", me=ctx.me)
        return [ctx.call("ask:conditions", Priority.INFO, text, ttl=40, verbosity=Verbosity.NORMAL)]

    def _ask_balance(self, ctx: Context, me: CarState) -> list[RadioCall]:
        if "balance" in self._asked or len(me.valid_laps()) < 2:
            return []
        self._asked.add("balance")
        text = ctx.say("ask_balance", me=ctx.me)
        return [ctx.call("ask:balance", Priority.INFO, text, ttl=60, verbosity=Verbosity.NORMAL)]
