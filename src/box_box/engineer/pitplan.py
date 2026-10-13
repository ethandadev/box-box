"""What a stop would look like right now: which tyres we'd fit, and where we'd rejoin and next to whom.

The pit wall answers "if we box now?" by subtracting the pit loss from the gaps to the cars behind. The
game predicts the rejoin position itself (Session.pit_rejoin_position); that also pins down roughly how
much time a stop costs at this track, so the gaps to the cars we'd come out between can be quoted too.
"""

from __future__ import annotations

from dataclasses import dataclass

from box_box.state.model import CarState, World
from box_box.telemetry import names

DEFAULT_PIT_LOSS_S = 22.0  # a typical stop with the pit lane, used until the game's prediction says otherwise
DRY = {16: "softs", 17: "mediums", 18: "hards"}
SOFTNESS = {16: 0, 17: 1, 18: 2}


@dataclass(frozen=True)
class Rejoin:
    position: int
    places_lost: int
    ahead: tuple[CarState, float] | None  # car we'd come out behind, and by how much (s)
    behind: tuple[CarState, float] | None  # car we'd come out ahead of, and by how much (s)
    pit_loss_s: float


@dataclass(frozen=True)
class NextTyre:
    compound: str  # "hards"
    fresh: bool


def _gaps_behind(world: World, me: CarState) -> list[tuple[CarState, float]]:
    assert me.lap
    out = []
    for car in world.running():
        if car.index == me.index or car.lap is None or car.lap.position <= me.lap.position:
            continue
        g = world.race_gap_s(me, car)
        if g is not None and g >= 0:
            out.append((car, g))
    return sorted(out, key=lambda e: e[1])


def pit_loss(world: World, me: CarState) -> float:
    """Seconds a stop costs here, pinned down by the game's predicted rejoin position when it has one:
    more than the gap to the last car we'd drop behind, less than the gap to the first we'd stay ahead of."""
    s = world.session
    behind = _gaps_behind(world, me)
    if s is None or me.lap is None or not s.pit_rejoin_position or not behind:
        return DEFAULT_PIT_LOSS_S
    lost = s.pit_rejoin_position - me.lap.position
    if lost < 0 or lost > len(behind):
        return DEFAULT_PIT_LOSS_S
    if lost == 0:
        return max(12.0, min(DEFAULT_PIT_LOSS_S, behind[0][1] - 0.5))
    lo = behind[lost - 1][1]
    hi = behind[lost][1] if lost < len(behind) else lo + 4.0
    return min(max((lo + hi) / 2, 12.0), 40.0)


def rejoin(world: World, me: CarState) -> Rejoin | None:
    if not world.is_race or me.lap is None:
        return None
    loss = pit_loss(world, me)
    behind = _gaps_behind(world, me)
    passed = [(c, g) for c, g in behind if g < loss]
    still_behind = [(c, g) for c, g in behind if g >= loss]
    ahead = (passed[-1][0], round(loss - passed[-1][1], 1)) if passed else None
    if ahead is None:  # nobody gets by: we'd still be behind the car that's ahead of us now
        front = world.car_at_position(me.lap.position - 1)
        gap = world.race_gap_s(front, me) if front is not None else None
        ahead = (front, round(gap + loss, 1)) if front is not None and gap is not None else None
    behind_car = (still_behind[0][0], round(still_behind[0][1] - loss, 1)) if still_behind else None
    return Rejoin(me.lap.position + len(passed), len(passed), ahead, behind_car, loss)


def next_tyre(world: World, me: CarState) -> NextTyre | None:
    """What we'd fit: the softest fresh set that lasts to the flag, a second compound if the rules need one."""
    if me.status is None:
        return None
    s = world.session
    if s is not None and s.weather >= 3:  # rain: inters for light, wets for heavy
        return NextTyre("wets" if s.weather >= 4 else "inters", True)
    sets = me.tyre_sets.sets if me.tyre_sets else ()
    used = {me.status.visual_compound}
    if me.history:
        used |= {st[2] for st in me.history.stints}
    used_dry = {c for c in used if c in DRY}
    wet_race = bool(used & {7, 8})  # inters or wets used: the two-compound rule no longer applies
    need_other = world.is_race and len(used_dry) < 2 and not world.is_sprint and not wet_race
    remaining = world.laps_remaining or 0
    candidates = [t for t in sets if t.available and not t.fitted and t.visual_compound in DRY]
    if need_other:
        candidates = [t for t in candidates if t.visual_compound not in used_dry] or candidates
    if not candidates:
        current = me.status.visual_compound
        if need_other:
            other = [c for c in (18, 17, 16) if c not in used_dry]
            return NextTyre(DRY[other[0]], True) if other else None
        return NextTyre(names.compound_name(current), False) if current in DRY else None
    lasting = [t for t in candidates if t.usable_life >= remaining + 1]
    if lasting:  # the softest set that makes the flag
        best = min(lasting, key=lambda t: (SOFTNESS[t.visual_compound], t.wear))
    else:  # nothing lasts: the hardest we have
        best = min(candidates, key=lambda t: (-SOFTNESS[t.visual_compound], t.wear))
    return NextTyre(DRY[best.visual_compound], best.wear < 5)


def near_words(r: Rejoin, rng) -> str:  # type: ignore[no-untyped-def]
    """'right behind Alonso', 'just ahead of Norris, it'll be close', 'in clean air'."""
    from box_box.engineer.radio import expand

    close = 1.5
    bits = []
    if r.ahead and r.ahead[1] <= 3.0:
        a, g = r.ahead
        if g <= close:
            bits.append(expand("[right behind|just behind|on the back of] ", rng) + a.name)
        else:
            bits.append(f"{g:.1f} behind {a.name}")
    if r.behind and r.behind[1] <= 3.0:
        b, g = r.behind
        if g <= close:
            bits.append(expand("[just ahead of|only just in front of|a fraction ahead of] ", rng) + b.name)
        else:
            bits.append(f"{g:.1f} ahead of {b.name}")
    if not bits:
        return expand("[in clean air|in a gap|with space around you]", rng)
    text = " and ".join(bits)
    if any(w in text for w in ("right", "just", "fraction", "only")):
        text += expand("[, it'll be close|, tight on the exit|, close on pit exit|]", rng)
    return text
