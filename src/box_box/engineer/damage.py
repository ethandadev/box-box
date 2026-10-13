"""Damage, said the way an engineer says it: how bad, what it costs, what we do about it.

The game reports damage as a percentage per part. On the radio nobody says "floor 23 percent": they say
"some slight damage on the floor, nothing too serious" or "front wing's pretty beaten up on the left,
that's a few tenths, box this lap". This turns the numbers into that, with plenty of ways to say each.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from box_box.engineer.radio import expand, tenths
from box_box.telemetry.packets import CarDamage

NOTICE_PCT = 3  # below this a part is clean as far as the radio is concerned


@dataclass(frozen=True)
class Part:
    key: str  # CarDamage field
    name: str  # how it's said
    cost_per_pct: float  # rough lap time lost per percent of damage (s); 0 for reliability parts
    reliability: bool = False


FRONT_WING = Part("front_wing", "front wing", 0.008)  # both sides, said as one part
PARTS = (
    Part("front_left_wing", "left side of the front wing", 0.008),
    Part("front_right_wing", "right side of the front wing", 0.008),
    Part("floor", "floor", 0.010),
    Part("diffuser", "diffuser", 0.008),
    Part("rear_wing", "rear wing", 0.006),
    Part("sidepod", "sidepod", 0.003),
    Part("engine", "engine", 0.0, reliability=True),
    Part("gearbox", "gearbox", 0.0, reliability=True),
)
BY_WORD = {
    "front wing": ("front_left_wing", "front_right_wing"),
    "wing": ("front_left_wing", "front_right_wing", "rear_wing"),
    "nose": ("front_left_wing", "front_right_wing"),
    "rear wing": ("rear_wing",),
    "floor": ("floor",),
    "diffuser": ("diffuser",),
    "sidepod": ("sidepod",),
    "engine": ("engine",),
    "power unit": ("engine",),
    "gearbox": ("gearbox",),
}

# How bad, in words. Each band has several ways in, so the same damage is never described the same way.
SEVERITY = [
    (10, "cosmetic", ["[a few marks|a little bit of damage|some minor scuffs|a tiny bit of damage|just a scratch]"]),
    (
        25,
        "slight",
        ["[slight damage|a bit of damage|some light damage|a small amount of damage|a little bit missing]"],
    ),
    (
        45,
        "moderate",
        ["[moderate damage|a fair bit of damage|a decent chunk of damage|a proper knock|some real damage]"],
    ),
    (70, "heavy", ["[heavy damage|a lot of damage|significant damage|it's badly damaged|big damage]"]),
    (101, "severe", ["[it's destroyed|there's not much of it left|it's hanging off|it's gone]"]),
]
VERDICT = {
    "cosmetic": "[nothing to worry about|you won't feel it|it's fine|nothing in it|no lap time in that]",
    "slight": "[nothing too serious|not a big deal|you'll barely feel it|it's costing very little|we can live with it]",
    "moderate": "[that's costing you {cost}|worth about {cost} a lap|you'll feel it in the quick stuff, about {cost}|maybe {cost} a lap]",
    "heavy": "[that's {cost} a lap at least|big loss of downforce, {cost} or more|you'll be fighting the car|that's costing a lot]",
    "severe": "[the car's going to be a handful|that's a lot of lap time|you'll be struggling for grip everywhere]",
}
RELIABILITY = {
    "cosmetic": "[it's fine for now|nothing to worry about yet|we're watching it]",
    "slight": "[we'll keep an eye on it|nothing urgent, we're watching it|not a concern for now]",
    "moderate": "[go easy on it, short shift where you can|look after it, short shift on exits|manage it, no kerbs]",
    "heavy": "[nurse it home, short shift everywhere|really look after it, we're worried|it may not last, go gently]",
    "severe": "[it might not make it|we may have to retire it|it's on its way out]",
}
CLEAN = [
    "[{part} looks fine|No damage on the {part}, it's clean|{part}'s all good from here|"
    "Nothing on the {part}, you're fine|{part} looks clean on the data]."
]
ALL_CLEAN = [
    "[Car looks okay from here|No damage showing|All clean on the data, you got away with it|"
    "Car's fine, no damage that we can see|Nothing showing, car looks good|We're clean, carry on]."
]


def severity(pct: int) -> str | None:
    if pct < NOTICE_PCT:
        return None
    for limit, name, _ in SEVERITY:
        if pct < limit:
            return name
    return "severe"


def _how_bad(name: str, rng: random.Random) -> str:
    return expand(next(t for _, n, t in SEVERITY if n == name)[0], rng)


def _entries(dmg: CarDamage, keys: tuple[str, ...] | None = None) -> list[tuple[Part, int]]:
    """(part, damage) for the parts asked about (or all), with the two halves of the front wing merged:
    named by side only when one side is clearly worse."""
    keys = keys or tuple(p.key for p in PARTS)
    out: list[tuple[Part, int]] = []
    left, right = dmg.front_left_wing, dmg.front_right_wing
    if "front_left_wing" in keys or "front_right_wing" in keys:
        if abs(left - right) < 10:
            out.append((FRONT_WING, max(left, right)))
        else:
            worse = PARTS[0] if left > right else PARTS[1]
            out.append((worse, max(left, right)))
    out += [(p, getattr(dmg, p.key)) for p in PARTS[2:] if p.key in keys]
    return out


def cost_s(dmg: CarDamage) -> float:
    return sum(getattr(dmg, p.key) * p.cost_per_pct for p in PARTS)


def _cost_words(seconds: float) -> str:
    return "a tenth" if seconds < 0.15 else tenths(seconds)


def part_line(part: Part, pct: int, rng: random.Random) -> str:
    """'We've got slight damage on the floor, nothing too serious.'"""
    level = severity(pct)
    if level is None:
        text = expand(rng.choice(CLEAN), rng).format(part=part.name)
        return text[:1].upper() + text[1:]
    how = _how_bad(level, rng)
    verdict = expand((RELIABILITY if part.reliability else VERDICT)[level], rng)
    verdict = verdict.format(cost=_cost_words(pct * part.cost_per_pct))
    if how.startswith("it's") or how.startswith("there's"):
        shape = rng.choice(["{Part}: {how}. {Verdict}.", "Looking at the {part}, {how}. {Verdict}."])
    else:
        shape = rng.choice(
            [
                "We've got {how} on the {part}, {verdict}.",
                "{How} on the {part}. {Verdict}.",
                "The {part} has {how}, {verdict}.",
                "[Right|So|And the], {part}: {how}. {Verdict}.",
                "There's {how} on the {part}. {Verdict}.",
            ]
        )
    text = expand(shape, rng).replace("And the,", "And the").format(
        part=part.name, Part=part.name.capitalize(), how=how, How=how.capitalize(),
        verdict=verdict, Verdict=verdict[:1].upper() + verdict[1:],
    )  # fmt: skip
    return text[:1].upper() + text[1:]


def needs_box(dmg: CarDamage, race: bool) -> bool:
    """Worth a stop: a front wing that's really hurting, or (in practice) any serious aero damage."""
    wing = max(dmg.front_left_wing, dmg.front_right_wing)
    if race:
        return wing >= 40
    return wing >= 40 or dmg.floor >= 45 or dmg.diffuser >= 45 or dmg.rear_wing >= 45


BOX_RACE = "[Box this lap for a new nose|Box, box, we'll change the front wing|Bring it in this lap, new nose]."
BOX_PRACTICE = "[Bring it in, we'll fix it|Box this lap, we'll sort the car|Come in, we'll change the parts]."


def report(
    dmg: CarDamage,
    rng: random.Random,
    *,
    race: bool,
    parts: tuple[str, ...] | None = None,
    since: CarDamage | None = None,
    max_parts: int = 2,
    advise_box: bool = True,
) -> str:
    """The damage, in words.

    `parts`: the driver asked about these (answer each, even if clean). `since`: only what changed after
    an impact. Otherwise the worst couple of parts and the overall verdict."""
    if parts:
        text = " ".join(part_line(p, pct, rng) for p, pct in _entries(dmg, parts))
    else:
        before = dict(_entries(since)) if since is not None else {}
        hurt = [
            (p, pct) for p, pct in _entries(dmg) if pct - before.get(p, 0) >= NOTICE_PCT and severity(pct) is not None
        ]
        if not hurt:
            return expand(rng.choice(ALL_CLEAN), rng)
        hurt.sort(key=lambda e: e[1] * (e[0].cost_per_pct or 0.012), reverse=True)
        text = " ".join(part_line(p, pct, rng) for p, pct in hurt[:max_parts])
        if len(hurt) > max_parts:
            text += " " + expand(
                "[A few bits elsewhere too, nothing major|Some small stuff elsewhere|Bits of damage elsewhere, minor].",
                rng,
            )
    asked_elsewhere = parts is not None and not ({"front_left_wing", "front_right_wing"} & set(parts))
    if advise_box and needs_box(dmg, race) and not (asked_elsewhere and race):
        text += " " + expand(BOX_RACE if race else BOX_PRACTICE, rng)
    return text


def parts_named(text: str) -> tuple[str, ...]:
    """Which parts the driver asked about ("check the front wing", "how's the floor?"), longest match first."""
    t = text.lower()
    for word in sorted(BY_WORD, key=len, reverse=True):
        if word in t:
            return BY_WORD[word]
    return ()
