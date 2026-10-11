"""Instant answers for the common questions and commands, no AI needed.

These run before Claude: they're immediate, they work offline and without an API key, and they're
exact (the numbers come straight from telemetry). Anything not matched here goes to Claude.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from box_box.brain import tools
from box_box.engineer.radio import gap, lap_time
from box_box.state.model import World


class Actions(Protocol):
    def set_verbosity(self, level: str) -> None: ...
    def mute(self, minutes: float) -> None: ...
    def unmute(self) -> None: ...
    def set_gap_every(self, laps: int) -> None: ...
    def set_callout(self, category: str, enabled: bool) -> None: ...
    def remember(self, note: str) -> None: ...


@dataclass
class Answer:
    text: str
    command: bool = False  # changed a setting (so it's handled even when Claude is available)


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "every": 1, "each": 1}


def _num(text: str, default: int) -> int:
    m = re.search(r"\b(\d+|one|two|three|four|five)\b", text)
    if not m:
        return default
    v = m.group(1)
    return int(v) if v.isdigit() else NUMBER_WORDS[v]


Rule = tuple[str, Callable[[str, World, Actions, str], Answer | None]]


def _radio_check(t: str, w: World, a: Actions, me: str) -> Answer:
    return Answer(f"Loud and clear, {me}.")


def _thanks(t: str, w: World, a: Actions, me: str) -> Answer:
    return Answer("No worries.")


def _quiet(t: str, w: World, a: Actions, me: str) -> Answer:
    a.set_verbosity("quiet")
    return Answer("Copy. Keeping it to the essentials.", command=True)


def _chatty(t: str, w: World, a: Actions, me: str) -> Answer:
    a.set_verbosity("chatty")
    return Answer("Copy, I'll keep you updated.", command=True)


def _normal(t: str, w: World, a: Actions, me: str) -> Answer:
    a.set_verbosity("normal")
    return Answer("Copy, back to normal.", command=True)


def _silence(t: str, w: World, a: Actions, me: str) -> Answer:
    minutes = _num(t, 5)
    a.mute(minutes)
    return Answer(f"Copy. Radio silence for {minutes} minutes, safety calls only.", command=True)


def _unmute(t: str, w: World, a: Actions, me: str) -> Answer:
    a.unmute()
    return Answer("Copy, I'm back.", command=True)


def _gap_every(t: str, w: World, a: Actions, me: str) -> Answer:
    laps = max(1, min(10, _num(t, 1)))
    a.set_gap_every(laps)
    return Answer("Copy, gaps every lap." if laps == 1 else f"Copy, gaps every {laps} laps.", command=True)


def _no_tyre_info(t: str, w: World, a: Actions, me: str) -> Answer:
    a.set_callout("tyres", False)
    return Answer("Copy, no more tyre updates unless it matters.", command=True)


def _position(t: str, w: World, a: Actions, me: str) -> Answer | None:
    info = tools.session_info(w)
    if "error" in info:
        return Answer("Stand by, I've got no timing yet.")
    return Answer(f"You're P{info['my_position']}.")


def _gap(t: str, w: World, a: Actions, me: str) -> Answer | None:
    g = tools.gaps(w)
    if "error" in g:
        return Answer("Stand by, no timing yet.")
    if not w.is_race:
        if g.get("fastest") and g.get("my_best"):
            if g["fastest"]["name"] == (w.player.name if w.player else ""):
                return Answer(f"You're quickest, {g['my_best']}.")
            return Answer(f"Your best is {g['my_best']}, {g['off_fastest_s']:.3f} off {g['fastest']['name']}.")
        return Answer("No lap time yet.")
    want_behind = bool(re.search(r"behind|back|following", t))
    want_ahead = bool(re.search(r"ahead|front|in front|leader", t)) or not want_behind
    parts = []
    if want_ahead and "ahead" in g:
        parts.append(f"{g['ahead']['name']} is {gap(g['ahead']['gap_s'])} ahead")
    if want_behind and "behind" in g:
        parts.append(f"{g['behind']['name']} is {gap(g['behind']['gap_s'])} behind")
    if "leader" in t and "to_leader_s" in g:
        parts = [f"{gap(g['to_leader_s'])} to the leader"]
    if not parts:
        if g.get("my_position") == 1:
            return Answer(
                f"You're leading. {g['behind']['name']} is {gap(g['behind']['gap_s'])} behind."
                if "behind" in g
                else "You're leading."
            )
        return Answer("Nobody close behind." if want_behind else "Nobody ahead.")
    return Answer(". ".join(parts) + ".")


def _driver_gap(t: str, w: World, a: Actions, me: str) -> Answer | None:
    m = re.search(r"(?:gap to|where(?:'s| is)|how far (?:is|to)|what about)\s+([a-z\- ]+?)(?:\?|$| now| at| doing)", t)
    if not m:
        return None
    who = m.group(1).strip()
    if who in (
        "the leader",
        "leader",
        "the car ahead",
        "the car behind",
        "car ahead",
        "car behind",
        "them",
        "him",
        "her",
    ):
        return None
    info = tools.driver(w, who)
    if "error" in info:
        return None
    if "gap_to_me_s" in info:
        return Answer(f"{info['name']} is P{info['position']}, {gap(abs(info['gap_to_me_s']))} {info['relation']}.")
    if info.get("best_lap"):
        return Answer(f"{info['name']} is P{info['position']}, best lap {info['best_lap']}.")
    return Answer(f"{info['name']} is P{info['position']}.")


def _tyres(t: str, w: World, a: Actions, me: str) -> Answer | None:
    info = tools.tyres(w)
    if "error" in info:
        return Answer("Stand by, no tyre data yet.")
    text = f"{info['compound'].capitalize()}, {info['age_laps']} laps old"
    if "average_wear_pct" in info:
        text += f", {info['average_wear_pct']} percent wear"
        worst = max(info["wear_pct"], key=info["wear_pct"].get)
        if info["wear_pct"][worst] - info["average_wear_pct"] >= 4:
            text += f", {worst.replace('_', ' ')} is the worst"
    if info.get("laps_until_70pct") is not None and w.is_race:
        text += f". About {info['laps_until_70pct']} laps left in them"
    temps = info.get("carcass_temp_c")
    if temps:
        hot = [c.replace("_", " ") for c, v in temps.items() if v > 112]
        cold = [c.replace("_", " ") for c, v in temps.items() if v < 75]
        if hot:
            text += ". Running hot on the " + " and ".join(hot)
        elif cold:
            text += ". Still a bit cold"
        else:
            text += ". Temperatures look good"
    return Answer(text + ".")


def _fuel(t: str, w: World, a: Actions, me: str) -> Answer | None:
    info = tools.fuel(w)
    if "error" in info:
        return Answer("Stand by, no fuel data.")
    if w.is_race:
        m = info["margin_laps_at_finish"]
        if m < -0.2:
            return Answer(f"We're about {abs(m):.1f} laps short. Lift and coast.")
        return Answer(f"Fuel's fine, about {m:.1f} laps in hand." if m > 0.3 else "Fuel's okay, right on target.")
    return Answer(f"{info['fuel_kg']} kilos on board, about {info['laps_of_fuel']} laps.")


def _damage(t: str, w: World, a: Actions, me: str) -> Answer | None:
    info = tools.car_health(w)
    if "error" in info:
        return Answer("Stand by, no car data.")
    bits = []
    for key, label in (
        ("front_wing_left_pct", "front wing left"),
        ("front_wing_right_pct", "front wing right"),
        ("rear_wing_pct", "rear wing"),
        ("floor_pct", "floor"),
        ("diffuser_pct", "diffuser"),
        ("sidepod_pct", "sidepod"),
        ("engine_pct", "engine"),
        ("gearbox_pct", "gearbox"),
    ):
        if info[key] >= 5:
            bits.append(f"{label} {info[key]} percent")
    if not bits:
        return Answer("Car looks fine, no damage.")
    return Answer("We've got " + ", ".join(bits) + ".")


def _weather(t: str, w: World, a: Actions, me: str) -> Answer | None:
    info = tools.weather(w)
    if "error" in info:
        return Answer("Stand by, no weather data.")
    rain = next(
        (f for f in info["forecast"] if f["in_min"] > 0 and f["rain_pct"] >= 40 and "rain" in f["weather"]), None
    )
    now = info["now"]
    if rain:
        return Answer(f"It's {now} now. Rain expected in about {rain['in_min']} minutes, {rain['rain_pct']} percent.")
    return Answer(f"It's {now}, track {info['track_temp_c']} degrees. No rain on the radar.")


def _laps_left(t: str, w: World, a: Actions, me: str) -> Answer | None:
    if w.is_race and w.laps_remaining is not None:
        n = w.laps_remaining
        return Answer("This is the last lap." if n <= 1 else f"{n} laps to go, including this one.")
    s = w.session
    if s:
        return Answer(f"{max(0, s.time_left_s // 60)} minutes left in the session.")
    return None


def _last_lap(t: str, w: World, a: Actions, me: str) -> Answer | None:
    info = tools.lap_times(w, None, 1)
    if "error" in info or not info["laps"]:
        return Answer("No lap time yet.")
    last = info["laps"][-1]
    text = f"Last lap {last['time']}"
    if not last["valid"]:
        text += ", deleted"
    if info.get("best"):
        text += f". Best is {info['best']}"
    return Answer(text + ".")


def _best_lap(t: str, w: World, a: Actions, me: str) -> Answer | None:
    me_car = w.player
    if me_car is None or not me_car.session_best_ms:
        return Answer("No lap time yet.")
    return Answer(f"Your best is {lap_time(me_car.session_best_ms)}.")


def _box(t: str, w: World, a: Actions, me: str) -> Answer:
    return Answer("Copy, box this lap. Box, box. Confirm on the MFD.")


def _stay_out(t: str, w: World, a: Actions, me: str) -> Answer:
    return Answer("Copy, stay out.")


def _energy(t: str, w: World, a: Actions, me: str) -> Answer | None:
    info = tools.energy(w)
    if "error" in info:
        return None
    text = f"Battery at {info['battery_pct']} percent"
    if info.get("overtake_available"):
        text += ", overtake is available"
    return Answer(text + ".")


def _whos_around(t: str, w: World, a: Actions, me: str) -> Answer | None:
    g = tools.gaps(w)
    if not w.is_race or "error" in g:
        return None
    bits = []
    if "ahead" in g:
        bits.append(f"{g['ahead']['name']} ahead")
    if "behind" in g:
        bits.append(f"{g['behind']['name']} behind")
    return Answer(", ".join(bits) + "." if bits else "Nobody around you.")


def _remember(t: str, w: World, a: Actions, me: str) -> Answer | None:
    m = re.search(r"remember (?:that )?(.+)", t)
    if not m:
        return None
    a.remember(m.group(1).strip().rstrip("."))
    return Answer("Copy, noted.", command=True)


RULES: list[Rule] = [
    (r"\bradio check\b|\bcan you hear me\b|\bdo you (?:read|copy)\b", _radio_check),
    (r"^(?:thanks|thank you|cheers|nice one)\b", _thanks),
    (r"\bremember\b", _remember),
    (r"\b(?:radio silence|stop talking|shut up|be quiet for|go quiet|no radio)\b", _silence),
    (r"\b(?:talk to me again|radio back on|you can talk|unmute|come back on)\b", _unmute),
    (
        r"\b(?:less (?:radio|talking|chat)|quiet(?:er)? please|too much (?:talking|radio)|only (?:the )?essentials)\b",
        _quiet,
    ),
    (r"\b(?:more (?:radio|updates|info)|talk more|chattier|keep me updated)\b", _chatty),
    (r"\bnormal (?:radio|amount)\b", _normal),
    (r"\bgaps? every\b", _gap_every),
    (r"\b(?:no more|stop the|stop telling me about|don'?t tell me about) tyres?\b", _no_tyre_info),
    (r"^(?:box|box box|boxing|i'?m boxing|box this lap|pitting|i'?m pitting)\b", _box),
    (r"^(?:stay(?:ing)? out|not boxing)\b", _stay_out),
    (r"\b(?:gap to|where(?:'s| is)|how far (?:is|to)|what about)\s+(?!the car|car |the leader|leader)\w", _driver_gap),
    (r"\bgaps?\b|\bhow far (?:ahead|behind)\b|\b(?:car|guy) (?:ahead|behind|in front)\b", _gap),
    (r"\bwhat position\b|\bwhere am i\b|\bwhat(?:'s| is) my position\b|\bwhat place\b|\bwhat p\b", _position),
    (r"\bwho(?:'s| is) (?:around|ahead|behind|in front)\b", _whos_around),
    (r"\btyres?\b|\btires?\b|\bwear\b|\bdeg\b", _tyres),
    (r"\bfuel\b|\bsave fuel\b|\blift and coast\b", _fuel),
    (r"\bdamage\b|\bwing\b|\bfloor\b|\bcar ok\b|\bcar okay\b|\bcar alright\b", _damage),
    (r"\bweather\b|\brain\b|\bforecast\b|\bradar\b", _weather),
    (
        r"\blaps? (?:left|to go|remaining)\b|\bhow many laps\b|\bhow long left\b|\btime left\b|\bminutes left\b",
        _laps_left,
    ),
    (r"\blast lap\b|\bwhat was that lap\b|\bwhat did i do\b|\blap time\b", _last_lap),
    (r"\bbest lap\b|\bfastest lap\b|\bmy best\b", _best_lap),
    (r"\bbattery\b|\bovertake mode\b|\bboost\b|\bers\b|\benergy\b", _energy),
]
_COMPILED = [(re.compile(p), fn) for p, fn in RULES]


def answer(text: str, world: World, actions: Actions, me: str) -> Answer | None:
    t = text.lower().strip()
    t = re.sub(r"[,.!]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for pattern, fn in _COMPILED:
        if pattern.search(t):
            result = fn(t, world, actions, me)
            if result is not None:
                return result
    return None
