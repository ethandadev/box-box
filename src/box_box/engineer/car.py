"""Car health: damage, punctures, tyre temperatures and wear, brakes, fuel, faults."""

from __future__ import annotations

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity
from box_box.state.model import CarState, LapCompleted
from box_box.telemetry.packets import CarDamage

CORNERS = ("rear left", "rear right", "front left", "front right")  # spec order RL, RR, FL, FR
SLICKS = {16, 17, 18, 19, 20, 21, 22}

# Carcass (inner) temperature windows in °C. Wets and inters run cooler.
HOT_SLICK, COLD_SLICK = 112, 75
HOT_WET, COLD_WET = 100, 55
BRAKES_HOT = 1050


class CarDetector(Detector):
    name = "car"

    def reset(self) -> None:
        self._last_damage: CarDamage | None = None
        self._last_call: dict[str, float] = {}
        self._hot_since: dict[str, float] = {}
        self._wear_called: set[int] = set()
        self._wear_stint = -1
        self._fault_seen = {"ers": False, "drs": False}
        self._fuel_call_lap = 0

    def on_rewind(self) -> None:
        # Flashbacks undo damage: re-baseline so restored parts don't look like new damage.
        self._last_damage = None
        self._hot_since.clear()

    def _cooldown(self, key: str, now: float, seconds: float) -> bool:
        """True (and arms the timer) if `key` hasn't been called in the last `seconds`."""
        if now - self._last_call.get(key, -1e9) < seconds:
            return False
        self._last_call[key] = now
        return True

    def check(self, ctx: Context) -> list[RadioCall]:
        me = ctx.world.player
        if me is None or me.lap is None:
            return []
        calls: list[RadioCall] = []
        if me.damage is not None:
            calls += self._damage(ctx, me, me.damage)
        if me.on_track and me.telemetry and me.status:
            calls += self._tyre_temps(ctx, me)
            calls += self._brakes(ctx, me)
        for change in ctx.changes:
            if isinstance(change, LapCompleted) and change.car_index == me.index:
                calls += self._wear(ctx, me)
                calls += self._fuel(ctx, me)
        return calls

    # -- damage -------------------------------------------------------------------------------------

    def _damage(self, ctx: Context, me: CarState, dmg: CarDamage) -> list[RadioCall]:
        prev, self._last_damage = self._last_damage, dmg
        if prev is None:
            return []
        out: list[RadioCall] = []
        race = ctx.world.is_race

        # Puncture: a tyre's damage jumps, or it's suddenly critical.
        for i, (a, b) in enumerate(zip(prev.tyre_damage, dmg.tyre_damage, strict=True)):
            if (b - a >= 25 or (b >= 60 > a)) and self._cooldown(f"puncture{i}", ctx.now, 60):
                out.append(
                    ctx.call(
                        "puncture", Priority.SAFETY, ctx.say("puncture", corner=CORNERS[i]), ttl=15, category="damage"
                    )
                )

        for side, a, b in (
            ("left", prev.front_left_wing, dmg.front_left_wing),
            ("right", prev.front_right_wing, dmg.front_right_wing),
        ):
            if b - a >= 8:
                if b >= 40 and race:
                    out.append(
                        ctx.call("wing", Priority.STRATEGY, ctx.say("wing_damage_box"), ttl=30, category="damage")
                    )
                elif b >= 40 and not ctx.world.is_time_trial:
                    # Practice/quali: no point running a broken car, the data and laps are worthless.
                    text = ctx.say("wing_damage_practice", side=side, pct=b)
                    out.append(ctx.call("wing", Priority.STRATEGY, text, ttl=30, category="damage"))
                else:
                    out.append(
                        ctx.call(
                            "wing", Priority.INFO, ctx.say("wing_damage", side=side, pct=b), ttl=20, category="damage"
                        )
                    )

        for part, a, b in (
            ("floor", prev.floor, dmg.floor),
            ("diffuser", prev.diffuser, dmg.diffuser),
            ("sidepod", prev.sidepod, dmg.sidepod),
            ("rear wing", prev.rear_wing, dmg.rear_wing),
        ):
            if b - a >= 10:
                out.append(
                    ctx.call(
                        f"dmg:{part}",
                        Priority.INFO,
                        ctx.say("floor_damage", part=part.capitalize(), pct=b),
                        ttl=20,
                        category="damage",
                    )
                )

        if dmg.engine - prev.engine >= 10:
            out.append(
                ctx.call("engine", Priority.INFO, ctx.say("engine_damage", pct=dmg.engine), ttl=30, category="damage")
            )
        if dmg.ers_fault and not self._fault_seen["ers"]:
            out.append(ctx.call("ers_fault", Priority.STRATEGY, ctx.say("ers_fault"), ttl=30, category="damage"))
        if dmg.drs_fault and not self._fault_seen["drs"] and race:
            out.append(ctx.call("drs_fault", Priority.INFO, ctx.say("drs_fault"), ttl=30, category="damage"))
        self._fault_seen = {"ers": dmg.ers_fault, "drs": dmg.drs_fault}
        return out

    # -- tyres --------------------------------------------------------------------------------------

    def _tyre_temps(self, ctx: Context, me: CarState) -> list[RadioCall]:
        tel, status = me.telemetry, me.status
        assert tel and status
        if me.lap and me.lap.lap_num <= 1 and ctx.world.is_race:
            return []  # tyres are always out of window at the start
        slick = status.visual_compound in SLICKS
        hot, cold = (HOT_SLICK, COLD_SLICK) if slick else (HOT_WET, COLD_WET)
        inner = tel.tyre_inner_temps
        rears, fronts = (inner[0] + inner[1]) / 2, (inner[2] + inner[3]) / 2
        out: list[RadioCall] = []
        for axle, temp, where in (("Rears", rears, "traction zones"), ("Fronts", fronts, "high-speed corners")):
            key = f"hot:{axle}"
            if temp > hot:
                since = self._hot_since.setdefault(key, ctx.now)
                if ctx.now - since > 8 and self._cooldown(key, ctx.now, 240):
                    out.append(
                        ctx.call(
                            key,
                            Priority.INFO,
                            ctx.say("tyres_overheating", axle=axle, where=where),
                            ttl=15,
                            category="tyres",
                            verbosity=Verbosity.NORMAL,
                        )
                    )
            else:
                self._hot_since.pop(key, None)
            # Cold tyres only matter once they should be up to temperature: on a push lap or mid-race.
            ckey = f"cold:{axle}"
            if temp < cold and me.speed_kph > 150 and (status.tyre_age_laps >= 1):
                since = self._hot_since.setdefault(ckey, ctx.now)
                if ctx.now - since > 20 and self._cooldown(ckey, ctx.now, 300):
                    out.append(
                        ctx.call(
                            ckey,
                            Priority.INFO,
                            ctx.say("tyres_cold", axle=axle.lower()),
                            ttl=15,
                            category="tyres",
                            verbosity=Verbosity.NORMAL,
                        )
                    )
            else:
                self._hot_since.pop(ckey, None)
        return out

    def _wear(self, ctx: Context, me: CarState) -> list[RadioCall]:
        if not ctx.world.is_race or me.damage is None or me.status is None:
            return []
        stint = me.pit_stops
        if stint != self._wear_stint:
            self._wear_stint = stint
            self._wear_called.clear()
        wear = me.damage.tyre_wear
        avg = sum(wear) / 4
        worst = CORNERS[max(range(4), key=lambda i: wear[i])]
        for threshold in (70, 50, 30):
            if avg >= threshold and threshold not in self._wear_called:
                self._wear_called |= {t for t in (30, 50, 70) if t <= threshold}
                if threshold >= 70:
                    text = ctx.say("tyre_wear_high", pct=round(avg), me=ctx.me)
                    return [ctx.call("wear", Priority.STRATEGY, text, ttl=30, category="tyres")]
                text = ctx.say("tyre_wear", pct=round(avg), worst=worst)
                return [ctx.call("wear", Priority.INFO, text, ttl=30, category="tyres", verbosity=Verbosity.NORMAL)]
        return []

    def _brakes(self, ctx: Context, me: CarState) -> list[RadioCall]:
        tel = me.telemetry
        assert tel
        hottest = max(range(4), key=lambda i: tel.brake_temps[i])
        key = "brakes"
        if tel.brake_temps[hottest] > BRAKES_HOT:
            since = self._hot_since.setdefault(key, ctx.now)
            if ctx.now - since > 5 and self._cooldown(key, ctx.now, 300):
                text = ctx.say("brakes_hot", corner=CORNERS[hottest].capitalize())
                return [ctx.call(key, Priority.INFO, text, ttl=10, category="tyres", verbosity=Verbosity.NORMAL)]
        else:
            self._hot_since.pop(key, None)
        return []

    # -- fuel ---------------------------------------------------------------------------------------

    def _fuel(self, ctx: Context, me: CarState) -> list[RadioCall]:
        world = ctx.world
        if not world.is_race or me.status is None or me.lap is None or me.lap.lap_num < 3 or not me.active:
            return []
        margin = me.status.fuel_remaining_laps  # in a race: laps of fuel beyond the finish
        lap = me.lap.lap_num
        if margin < -0.3 and lap - self._fuel_call_lap >= 4:
            self._fuel_call_lap = lap
            text = ctx.say("fuel_short", laps=f"{abs(margin):.1f}")
            return [ctx.call("fuel", Priority.STRATEGY, text, ttl=30, category="fuel")]
        remaining = world.laps_remaining
        if (
            margin > 1.0
            and remaining is not None
            and 3 <= remaining <= world.total_laps // 2
            and self._fuel_call_lap == 0
        ):
            self._fuel_call_lap = lap
            return [
                ctx.call(
                    "fuel", Priority.COLOUR, ctx.say("fuel_ok"), ttl=30, category="fuel", verbosity=Verbosity.CHATTY
                )
            ]
        return []
