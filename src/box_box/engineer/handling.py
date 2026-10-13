"""Wheelspin and lock-ups, corner by corner, and the diff or bias change that fixes them.

The extended motion packet gives the player's wheel slip every frame: the rears spinning up on a corner
exit, a front locking into a braking zone. One moment is just a moment; the same thing at the same corner
lap after lap is what an engineer picks up on: "Rears are going out of 14, take some diff off, try 65."
"""

from __future__ import annotations

from collections import Counter

from box_box.engineer import advice
from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity
from box_box.state import tracks
from box_box.state.model import CarState, Slide, World

SPIN_SLIP = 0.15  # rear slip ratio under power: the rears are spinning up
LOCK_SLIP = -0.25  # under braking: a wheel is locking
MIN_KPH = 50
REARM_S = 0.6  # one slide is one event, however many frames it lasts
EXIT_M = 250.0  # wheelspin this far after a corner marker belongs to that corner
ENTRY_M = 250.0  # a lock-up this far before a corner marker belongs to that corner
KEEP_LAPS = 5
REPEATS = 3  # the same thing at the same corner this many times in RECENT_LAPS before we say anything
COOLDOWN_S = 240.0


def corner_near(world: World, distance: float, after: bool) -> str | None:
    """The corner a slide belongs to: the one just behind us for wheelspin (the exit), the one ahead for a
    lock-up (the braking zone)."""
    track = tracks.get(world.session.track_id) if world.session else None
    if track is None or distance < 0:
        return None
    length = world.track_length or track.length_m
    best: tuple[float, tracks.Corner] | None = None
    for c, at in track.corners_on(world.track_length or None):
        d = (distance - at) % length if after else (at - distance) % length
        if d <= (EXIT_M if after else ENTRY_M) and (best is None or d < best[0]):
            best = (d, c)
    return best[1].label if best else None


class HandlingDetector(Detector):
    name = "handling"

    def reset(self) -> None:
        self._active: dict[str, float] = {}  # kind -> session time last seen, while a slide is going on
        self._last_call: dict[str, float] = {}

    def on_rewind(self) -> None:
        self._active = {}  # what happened before the flashback still happened to the car; keep the calls' cooldowns

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        me = world.player
        if me is None or me.lap is None or me.telemetry is None or world.wheel_slip is None:
            return []
        if not me.active or me.in_pit_lane:
            return []
        kind = self._slide(me, world)
        if kind is None:
            return []
        corner = corner_near(world, me.lap.lap_distance, after=kind == "wheelspin")
        world.slides.append(Slide(me.lap.lap_num, kind, corner, world.session_time))
        world.slides[:] = [s for s in world.slides if s.lap > me.lap.lap_num - KEEP_LAPS][-60:]
        return self._advise(ctx, me, kind, corner)

    def _slide(self, me: CarState, world: World) -> str | None:
        """A new wheelspin or lock-up starting this tick, or None."""
        t, slip, now = me.telemetry, world.wheel_slip, world.session_time
        assert t is not None and slip is not None
        rl, rr, fl, fr = slip.slip_ratio
        found = []
        if t.speed_kph >= MIN_KPH:
            if t.throttle > 0.5 and t.brake < 0.05 and max(rl, rr) > SPIN_SLIP:
                found.append("wheelspin")
            if t.brake > 0.2:
                if min(fl, fr) < LOCK_SLIP:
                    found.append("front_lock")
                elif min(rl, rr) < LOCK_SLIP:
                    found.append("rear_lock")
        new = None
        for kind in found:
            if now - self._active.get(kind, -1e9) > REARM_S:
                new = kind
            self._active[kind] = now
        return new

    def _advise(self, ctx: Context, me: CarState, kind: str, corner: str | None) -> list[RadioCall]:
        world = ctx.world
        if corner is None or (world.is_race and me.lap is not None and me.lap.lap_num <= 2):
            return []
        same = [s for s in advice.recent(world, kind) if s.corner == corner]
        laps = Counter(s.lap for s in same)
        if len(same) < REPEATS or len(laps) < 2:
            return []
        now = world.session_time
        if now - self._last_call.get(kind, -1e9) < COOLDOWN_S:
            return []
        self._last_call[kind] = now
        text = advice.from_slides(world, me, kind, corner, ctx.say.rng)
        return [ctx.call("setup", Priority.INFO, text, ttl=25, category="setup", verbosity=Verbosity.NORMAL)]
