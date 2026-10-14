"""Setup and mode advice the way it's given on the radio: "Diff down to 65, should help the rears out of
turn 14", "Bias back two", "Energy mode medium", "Fuel map lean, and lift and coast".

What the driver can change from the wheel: the on-throttle diff and the brake bias (the MFD), the ERS
deploy mode and the fuel mix. The numbers come from the setup and status packets; the symptoms come from
what the driver says or, failing that, from the wheelspin and lock-ups the car has actually had.
"""

from __future__ import annotations

import random
import re
from collections import Counter

from box_box.engineer.radio import expand, gap, no_data
from box_box.state import tracks
from box_box.state.model import CarState, Slide, World
from box_box.telemetry.packets import DriverStatus

DIFF_MIN, DIFF_MAX = 10, 100  # on-throttle diff, percent
BIAS_MIN, BIAS_MAX = 50, 70  # front brake bias, percent
BATTERY_J = 4e6  # a full ERS store
ERS_LOW_PCT = 20  # below this, a hotlap/overtake mode is just emptying the battery
RECENT_LAPS = 3

MIX = {0: "lean", 1: "standard", 2: "rich", 3: "max"}


def ers_mode_name(mode: int, world: World) -> str:
    return {0: "none", 1: "medium", 2: "hotlap", 3: "boost" if world.packet_format >= 2026 else "overtake"}.get(
        mode, "medium"
    )


def battery_pct(me: CarState) -> int:
    return round(me.status.ers_store_j / BATTERY_J * 100) if me.status else 0


def _snap(value: int, step: int = 5) -> int:
    return int(round(value / step) * step)


# ---- what the car has been doing ------------------------------------------------------------------------


def recent(world: World, kind: str, laps: int = RECENT_LAPS) -> list[Slide]:
    me = world.player
    lap = me.lap.lap_num if me and me.lap else 0
    return [s for s in world.slides if s.kind == kind and s.lap > lap - laps]


def worst_corner(slides: list[Slide]) -> str | None:
    """The corner it keeps happening at; None if it's been a one-off here and there."""
    named = Counter(s.corner for s in slides if s.corner).most_common(1)
    return named[0][0] if named and named[0][1] >= 2 else None


def keeps_happening(slides: list[Slide]) -> bool:
    """Enough to change something for: twice at the same corner, or three times anywhere."""
    return len(slides) >= 3 or worst_corner(slides) is not None


NUMBERS = {1: "one", 2: "two", 3: "three"}


# ---- what the driver said ------------------------------------------------------------------------------

WHEELSPIN = re.compile(
    r"wheel ?spin|spinning (?:up )?(?:the )?(?:rears|wheels|tyres)|traction(?! control)|"
    r"rears? (?:go|goes|going|step|steps|stepping|kick|kicks|kicking|let go|snap|snaps)|snap(?:py|s|ping)? (?:on|out of|on the) exit|"
    r"oversteer (?:on|out of) (?:the )?exits?|exit oversteer|power oversteer|on the power"
)
EXIT_UNDERSTEER = re.compile(r"understeer (?:on|out of) (?:the )?exits?|exit understeer|won'?t rotate|doesn'?t rotate")
FRONT_LOCK = re.compile(
    r"lock(?:ing|s|ed)? (?:up )?(?:the )?fronts?|fronts? (?:are |keep )?lock|front lock|lock(?:ing|s)? up|lock-?ups?|locking"
)
REAR_LOCK = re.compile(
    r"lock(?:ing|s|ed)? (?:up )?(?:the )?rears?|rear(?:s|'s| end| wheels?| tyres?)? (?:is |are |keeps? )?lock|rear lock"
)
UNSTABLE_ENTRY = re.compile(
    r"unstable (?:on|under|into) (?:the )?brak|loose (?:on|into) (?:the )?entry|rear (?:is )?(?:loose|light) (?:on|into)|"
    r"(?:snap|oversteer) (?:on|into) (?:the )?entry|entry oversteer"
)
ENTRY_UNDERSTEER = re.compile(r"understeer (?:on|into) (?:the )?entry|entry understeer|won'?t turn in|doesn'?t turn in")

DIFF_WORDS = re.compile(r"\bdiff(?:erential)?s?\b")
BIAS_WORDS = re.compile(r"\b(?:brake )?bias\b|\bbrak(?:e|es|ing) (?:advice|balance|setting)")
ERS_WORDS = re.compile(
    r"\b(?:ers|energy|deploy\w*|battery) (?:mode|map|advice|setting|tips?)\b|\bwhat (?:ers|energy) mode\b|"
    r"\bhot ?lap mode\b|\bwhich mode\b|\bwhat mode\b|\bshould i (?:use|be in) (?:overtake|hot ?lap|medium)\b"
)
FUEL_WORDS = re.compile(
    r"\bfuel (?:map|mode|mix|advice|setting)\b|\b(?:lean|rich) (?:mix|map|mode)\b|"
    r"\b(?:will|does|can|do) (?:we|i) (?:make|last) (?:it )?(?:to )?the end\b(?!.*\btyres?\b)|\b(?:will|does) the fuel (?:make|last)\b|\benough fuel\b"
)


def corner_named(text: str, world: World) -> str | None:
    """'out of turn 14', 'into T1', 'at the hairpin': the corner the driver mentioned, said our way."""
    m = re.search(r"\b(?:turn|t) ?(\d{1,2}[ab]?)\b", text)
    if m:
        return f"turn {m.group(1).upper()}"
    track = tracks.get(world.session.track_id) if world.session else None
    if track:
        for c in track.corners:
            if c.name and c.name.lower() in text:
                return c.name
    return None


# ---- the advice ----------------------------------------------------------------------------------------


def diff(world: World, me: CarState, rng: random.Random, symptom: str | None = None, corner: str | None = None) -> str:
    setup = me.setup
    if setup is None:
        return no_data(rng, "setup")
    now = setup.on_throttle_diff
    spins = recent(world, "wheelspin")
    if symptom is None and keeps_happening(spins):
        symptom = "wheelspin"
    corner = corner or worst_corner(spins)
    if symptom in ("wheelspin", "exit_understeer"):
        step = 10 if now > 50 else 5
        new = max(DIFF_MIN, _snap(now - step))
        if new >= now:
            where = f" out of {corner}" if corner else " on the exits"
            return expand(
                f"[Diff's already right down at {now}|We're at the bottom of the diff already]. "
                f"[Short shift|Be patient with the throttle|Short shift and feed the throttle in]{where}.",
                rng,
            )
        if symptom == "wheelspin":
            why = (
                f"[Should help with the rears out of {corner}|That'll calm the rears out of {corner}|"
                f"Should help the traction out of {corner}|Should tidy up the exit of {corner}]"
                if corner
                else "[Should help with the rears on exit|That'll calm the wheelspin|Should help the traction]"
            )
        else:
            why = f"[Should help it rotate on exit|Should free the car up on exit{f' at {corner}' if corner else ''}]"
        change = f"[Lower the diff, try {new}|Take some diff off, try {new}|Diff down to {new}|Come down on the diff, {new}|Bring the diff down to {new}]"
        text = f"{change}. {why}."
        if rng.random() < 0.3:
            text += " [And short shift out of the slow stuff|Be gentle with the throttle too]."
        return expand(text, rng)
    if spins:
        where = f" out of {corner}" if corner else ""
        return expand(f"Diff's at {now}. [Bit of wheelspin{where}, take five off if it keeps happening|"
                      f"Odd moment of wheelspin{where}, nothing to change yet].", rng)  # fmt: skip
    return expand(
        f"[Diff's at {now}|On-throttle diff is {now}], [it's fine where it is|looks right from here|leave it]. "
        "[If the rears go on exit, take five off|If you get wheelspin, come down a bit|"
        "If it won't rotate on exit, take some off].",
        rng,
    )


def bias(world: World, me: CarState, rng: random.Random, symptom: str | None = None, corner: str | None = None) -> str:
    if me.status is None:
        return no_data(rng)
    now = me.status.front_brake_bias
    fronts, rears = recent(world, "front_lock"), recent(world, "rear_lock")
    if symptom is None:
        if keeps_happening(fronts) and len(fronts) >= len(rears):
            symptom = "front_lock"
        elif keeps_happening(rears):
            symptom = "rear_lock"
    if symptom == "front_lock":
        corner = corner or worst_corner(fronts)
        new = max(BIAS_MIN, now - 2)
        n = NUMBERS.get(now - new, str(now - new))
        why = (
            f"[Should stop the fronts locking into {corner}|That should sort the lock-ups into {corner}|"
            f"Should help the braking into {corner}]"
            if corner
            else "[Should stop the fronts locking|That'll help the lock-ups|Should calm the fronts]"
        )
        if new >= now:
            return expand(f"Bias is already back at {now}. [Brake a touch earlier|Ease off the pedal as you turn in]"
                          f"{f' into {corner}' if corner else ''}.", rng)  # fmt: skip
        return expand(
            f"[Bias back {n}, to {new}|Brake bias back {n}, {new}|Take {n} off the bias, so {new}|Bias back {n}, {new}]. {why}.",
            rng,
        )
    if symptom in ("rear_lock", "unstable_entry"):
        corner = corner or worst_corner(rears)
        new = min(BIAS_MAX, now + (2 if symptom == "rear_lock" else 1))
        if symptom == "rear_lock":
            why = (
                f"[Rears are locking into {corner}|That'll settle the rear into {corner}|Should calm the rear into {corner}]"
                if corner
                else "[Should settle the rear on entry|That'll calm the rear under braking]"
            )
        else:  # he said it's loose on entry, not that it's locking
            why = (
                f"[That'll settle the rear into {corner}|Should calm the rear into {corner}]"
                if corner
                else "[Should settle the rear on entry|That'll calm the rear under braking]"
            )
        if new <= now:
            return expand(
                f"Bias is already forward at {now}. [Smooth on the release|Ease off the brake as you turn in].", rng
            )
        n = NUMBERS.get(new - now, str(new - now))
        return expand(
            f"[Bias forward {n}, to {new}|Brake bias forward {n}, {new}|Move the bias forward {n}, so {new}]. {why}.",
            rng,
        )
    if symptom == "entry_understeer":
        new = max(BIAS_MIN, now - 1)
        if new >= now:
            return expand(
                f"Bias is already back at {now}. [Trail the brake into the apex|Release the brake a touch later].", rng
            )
        return expand(
            f"[Bias back one, to {new}|Take one off the bias, {new}]. [Should help it turn in|Gives you a bit more rotation on entry].",
            rng,
        )
    return expand(
        f"[Bias is {now}|Brake bias is at {now}], [looks about right|that's fine|happy with that]. "
        "[If the fronts start locking, take one off|Lock the fronts and we go back a click|If the rear's nervous on entry, go forward one].",
        rng,
    )


def ers(world: World, me: CarState, rng: random.Random) -> str:
    if me.status is None or me.lap is None:
        return no_data(rng)
    pct = battery_pct(me)
    mode = me.status.ers_deploy_mode
    name = ers_mode_name(mode, world)
    top = ers_mode_name(3, world)
    if pct < ERS_LOW_PCT and mode >= 2:
        return expand(f"[Energy mode medium|ERS medium|Energy map medium], [battery's at {pct}|we're down to {pct} percent]. "
                      "[Charge it back up|Let it recharge|Get some back].", rng)  # fmt: skip
    if world.is_race:
        ahead = world.car_at_position(me.lap.position - 1)
        behind = world.car_at_position(me.lap.position + 1)
        g_ahead = world.race_gap_s(ahead, me) if ahead else None
        g_behind = world.race_gap_s(me, behind) if behind else None
        if g_ahead is not None and g_ahead < 1.0 and pct > 30:
            return expand(f"[{top.capitalize()} mode on the straight|Use {top} on the straight|Go {top} down the straight], "
                          f"[{ahead.name}'s {gap(g_ahead)} ahead|you're {gap(g_ahead)} off {ahead.name}]. "  # type: ignore[union-attr]
                          "[Back to medium after|Medium again once you're through].", rng)  # fmt: skip
        if g_behind is not None and g_behind < 1.0:
            return expand(f"[Stay in medium|Medium's right], [keep some for defending|save it for defending], "
                          f"[{behind.name}'s {gap(g_behind)} behind|{behind.name}'s close].", rng)  # type: ignore[union-attr]  # fmt: skip
        if pct > 80 and mode < 2:
            return expand(
                f"[Battery's full|You've got {pct} percent], [hotlap mode for a lap or two|use it, hotlap for a lap].",
                rng,
            )
        return expand(
            f"[Medium's right|Stay in medium|Energy mode medium is fine], [battery's {pct}|you're on {pct} percent].",
            rng,
        )
    status = me.lap.driver_status
    if status == DriverStatus.IN_LAP:
        return expand(
            "[Medium or none on the in lap|Just medium on the way in], [let it charge|no need to deploy].", rng
        )
    if status == DriverStatus.OUT_LAP or me.in_pit_lane:
        return expand(
            "[Medium on the out lap|Stay in medium for now], [arrive with a full battery|charge it up for the push].",
            rng,
        )
    if pct >= 50 and mode < 2:
        return expand(
            f"[You've got {pct} percent|Battery's at {pct}], [hotlap mode for the push|go hotlap mode for this one].",
            rng,
        )
    said = "no deploy" if mode == 0 else f"{name} mode"
    return expand(f"[You're in {said}|It's {said}], battery's {pct}. [That's fine|Happy with that].", rng)


def _laps_of_fuel(laps: float) -> str:
    n = round(laps)
    if n < 1:
        return "under a lap"
    return "about a lap" if n == 1 else f"about {n} laps"


def fuel(world: World, me: CarState, rng: random.Random) -> str:
    s = me.status
    if s is None:
        return no_data(rng, "fuel")
    mix = MIX.get(s.fuel_mix, "standard")
    if not world.is_race:
        return expand(
            f"{s.fuel_in_tank:.1f} kilos, {_laps_of_fuel(s.fuel_remaining_laps)}. [{mix.capitalize()} map's fine|Map's fine].",
            rng,
        )
    m = s.fuel_remaining_laps
    if m < -0.2:
        if s.fuel_mix >= 1:
            return expand(f"[Fuel map lean|Lean mix, please|Switch to fuel lean]{', not ' + mix if rng.random() < 0.4 else ''}. "
                          f"[And lift and coast into the big stops|Lift and coast too|Plus some lift and coast]. "
                          f"We're [about|roughly] {abs(m):.1f} laps short.", rng)  # fmt: skip
        return expand(f"[Already on lean, so more lift and coast|You're on lean already, lift and coast more]. "
                      f"[Lift a bit earlier into the big stops|Lift fifty metres earlier into the heavy braking zones]. "
                      f"[About|Still] {abs(m):.1f} short.", rng)  # fmt: skip
    remaining = world.laps_remaining or 0
    if m > 0.8 and s.fuel_mix == 0:
        return expand(
            f"[Fuel's good now|We've got fuel back], [back to standard|standard map again]. {m:.1f} laps in hand.", rng
        )
    if m > 1.5 and s.fuel_mix <= 1 and 0 < remaining <= 5:
        return expand(
            "[You've got fuel in hand|Plenty of fuel], [rich mix to the end if you need it|go rich if you need the pace].",
            rng,
        )
    if s.fuel_mix == 0:
        return expand("[Stay on lean|Keep it lean], [we're right on the number|we're on target].", rng)
    return expand(
        f"[{mix.capitalize()}'s fine|Stay on {mix}], [fuel's on target|we'll make the end|no saving needed].", rng
    )


# ---- radio calls ----------------------------------------------------------------------------------------


def ers_mode_down(me: CarState, world: World, rng: random.Random) -> str | None:
    """'Energy mode medium': the battery's nearly flat and he's still deploying hard."""
    if me.status is None or me.status.ers_deploy_mode < 2 or battery_pct(me) >= ERS_LOW_PCT:
        return None
    return expand(
        "[Energy mode medium|ERS medium, please|Energy map medium|Mode medium], "
        "[battery's low|battery's nearly empty|we need to charge|you're running out of battery].",
        rng,
    )


def fuel_short(me: CarState, laps: float, rng: random.Random) -> str | None:
    """The map change that goes with 'we need to save fuel', when there's one to make."""
    if me.status is None or me.status.fuel_mix < 1:
        return None
    return expand(
        f"[Fuel map lean|Switch to fuel lean|Lean mix, please|Fuel map lean, not {MIX.get(me.status.fuel_mix, 'standard')}]. "
        f"[And lift and coast, we're|Lift and coast too, we're|Plus lift and coast,] {laps:.1f} laps short.",
        rng,
    )


def from_slides(world: World, me: CarState, kind: str, corner: str | None, rng: random.Random) -> str:
    """The proactive call after the same problem at the same corner, lap after lap."""
    if kind == "wheelspin":
        return diff(world, me, rng, symptom="wheelspin", corner=corner)
    return bias(world, me, rng, symptom=kind, corner=corner)


# ---- the driver asks ------------------------------------------------------------------------------------


def answer(text: str, world: World, rng: random.Random) -> str | None:
    """Diff, bias, ERS or fuel-map advice for what the driver said, or None if it isn't that kind of ask."""
    me = world.player
    if me is None:
        return None
    t = text.lower()
    corner = corner_named(t, world)
    if REAR_LOCK.search(t):
        return bias(world, me, rng, "rear_lock", corner)
    if FRONT_LOCK.search(t):
        return bias(world, me, rng, "front_lock", corner)
    if UNSTABLE_ENTRY.search(t):
        return bias(world, me, rng, "unstable_entry", corner)
    if ENTRY_UNDERSTEER.search(t):
        return bias(world, me, rng, "entry_understeer", corner)
    if EXIT_UNDERSTEER.search(t):  # first: "understeer on the power" isn't wheelspin
        return diff(world, me, rng, "exit_understeer", corner)
    if WHEELSPIN.search(t):
        return diff(world, me, rng, "wheelspin", corner)
    if DIFF_WORDS.search(t):
        return diff(world, me, rng, None, corner)
    if BIAS_WORDS.search(t):
        return bias(world, me, rng, None, corner)
    if ERS_WORDS.search(t):
        return ers(world, me, rng)
    if FUEL_WORDS.search(t):
        return fuel(world, me, rng)
    return None
