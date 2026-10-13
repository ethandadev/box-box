"""Car health: damage, punctures, tyre temperatures and wear, brakes, fuel, faults."""

from __future__ import annotations

from collections import deque

from box_box.engineer import advice, damage
from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity, expand
from box_box.state.model import CarState, LapCompleted
from box_box.telemetry.packets import CarDamage

CORNERS = ("rear left", "rear right", "front left", "front right")  # spec order RL, RR, FL, FR
SLICKS = {16, 17, 18, 19, 20, 21, 22}

# Carcass (inner) temperature windows in °C. Wets and inters run cooler.
HOT_SLICK, COLD_SLICK = 112, 75
HOT_WET, COLD_WET = 100, 55
BRAKES_HOT = 1050
REPORT_DELAY_S = (3.5, 6.0)  # after an impact, the pit wall takes a moment to read the data


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
        self._fuel_was_short = False
        self._speeds: deque[tuple[float, float]] = deque()  # (session time, kph) over the last moment
        self._report_due: float | None = None  # when to come back with the damage after an impact
        self._report_base: CarDamage | None = None  # the car before the impact
        self._big_hit = False

    def on_rewind(self) -> None:
        # Flashbacks undo damage: re-baseline so restored parts don't look like new damage.
        self._last_damage = None
        self._hot_since.clear()
        self._speeds.clear()
        self._report_due, self._report_base, self._big_hit = None, None, False

    def _cooldown(self, key: str, now: float, seconds: float) -> bool:
        """True (and arms the timer) if `key` hasn't been called in the last `seconds`."""
        last = self._last_call.get(key)
        if last is not None and 0 <= now - last < seconds:  # (a call from before a flashback doesn't count)
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
        out: list[RadioCall] = []
        now = ctx.now
        speed = me.speed_kph
        self._speeds.append((now, speed))
        while self._speeds and now - self._speeds[0][0] > 0.8:
            self._speeds.popleft()
        if prev is None:
            return out

        # Puncture: a tyre's damage jumps. Damage also creeps up with wear (a 20-lap-old hard reads 60%),
        # so a slow climb past a threshold is a worn tyre, not a puncture.
        for i, (a, b) in enumerate(zip(prev.tyre_damage, dmg.tyre_damage, strict=True)):
            sudden = b - a >= 25 or (b >= 85 > a and b - dmg.tyre_wear[i] >= 30)
            if sudden and self._cooldown(f"puncture{i}", now, 60):
                out.append(
                    ctx.call(
                        "puncture", Priority.SAFETY, ctx.say("puncture", corner=CORNERS[i]), ttl=15, category="damage"
                    )
                )

        # An impact: new damage, a contact event, or the car stopping dead. Like a real pit wall: check on
        # the driver first if it was a big one, then come back with what the data shows once it's settled.
        jump = max(
            (getattr(dmg, k) - getattr(prev, k) for k in ("front_left_wing", "front_right_wing", "floor",
                                                          "diffuser", "sidepod", "rear_wing")),
            default=0,
        ) + max(0, dmg.engine - prev.engine) // 2  # fmt: skip
        stopped = max((v for _, v in self._speeds), default=speed) - speed
        contact = any(e.code == "COLL" and me.index in (e.vehicle_idx, e.other_vehicle_idx) for e in ctx.events)
        hit = jump >= 8 or (dmg.engine - prev.engine) >= 10 or (contact and jump >= 3)
        big = jump >= 25 or (stopped >= 120 and speed < 100) or (contact and jump >= 15)
        if (hit or big) and self._report_due is None:
            self._report_base = prev
            self._report_due = now + ctx.say.rng.uniform(*REPORT_DELAY_S)
            self._big_hit = big
            if big and self._cooldown("impact", now, 30):
                out.append(
                    ctx.call("impact", Priority.STRATEGY, ctx.say("impact_check", me=ctx.me), ttl=4, category="damage")
                )
        elif big and self._report_due is not None:
            self._big_hit = True
        if self._report_due is not None and now >= self._report_due:
            base, big_hit = self._report_base, self._big_hit
            self._report_due, self._report_base, self._big_hit = None, None, False
            fresh = base is None or any(
                getattr(dmg, p.key) - getattr(base, p.key) >= damage.NOTICE_PCT for p in damage.PARTS
            )
            if fresh or big_hit:
                race = ctx.world.is_race
                left = ctx.world.laps_remaining if race else None
                last_laps = left is not None and left <= 2  # a new nose costs more than it gains now
                urgent = damage.needs_box(dmg, race) and not ctx.world.is_time_trial and not last_laps
                # Told to box a moment ago: don't repeat it with every bit of extra damage.
                advise = urgent and self._cooldown("advise_box", now, 90)
                text = damage.report(dmg, ctx.say.rng, race=race, since=base, advise_box=advise)
                out.append(
                    ctx.call(
                        "damage",
                        Priority.STRATEGY if urgent else Priority.INFO,
                        text,
                        ttl=30,
                        category="damage",
                    )
                )

        race = ctx.world.is_race
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
                            ctx.say("tyres_overheating", axle=axle.lower(), where=where),
                            ttl=15,
                            category="tyres",
                            verbosity=Verbosity.NORMAL,
                        )
                    )
            else:
                self._hot_since.pop(key, None)
            # Cold tyres only matter once they should be up to temperature: on a push lap or mid-race.
            ckey = f"cold:{axle}"
            under_sc = ctx.world.session is not None and ctx.world.session.safety_car_status in (1, 2)
            if temp < cold and me.speed_kph > 150 and status.tyre_age_laps >= 1 and not under_sc:
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
                    left = ctx.world.laps_remaining
                    if left is not None and left <= 2:
                        return []  # "box soon" with the flag in sight: just bring it home
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
                text = ctx.say("brakes_hot", corner=CORNERS[hottest])
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
            self._fuel_was_short = True
            # On standard or richer there's a map to change too: "Fuel map lean, and lift and coast."
            text = advice.fuel_short(me, abs(margin), ctx.say.rng) or ctx.say("fuel_short", laps=f"{abs(margin):.1f}")
            return [ctx.call("fuel", Priority.STRATEGY, text, ttl=30, category="fuel")]
        remaining = world.laps_remaining
        if self._fuel_was_short and margin > 0.8 and me.status.fuel_mix == 0 and lap - self._fuel_call_lap >= 2:
            self._fuel_was_short = False  # saved enough: give the pace back
            self._fuel_call_lap = lap
            text = expand(
                "[Fuel's recovered|We've saved enough fuel|Fuel's good now], [back to standard|standard map again|"
                "fuel map standard]. [You can stop the lift and coast|No more saving needed].",
                ctx.say.rng,
            )
            return [ctx.call("fuel", Priority.INFO, text, ttl=30, category="fuel", verbosity=Verbosity.NORMAL)]
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
