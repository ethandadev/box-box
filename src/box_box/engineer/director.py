"""The radio director: decides what the engineer says, and when.

Real engineers are good because of when they talk. The rules here:

- Safety calls go out immediately and can cut off anything less urgent.
- Everything else waits for a straight (no brake, wheel near centre, throttle pinned), unless the
  setting is off or the driver isn't in a high-workload moment (pits, garage, parked).
- Strategy calls and answers to the driver wait for a straight only briefly, then go anyway.
- Non-urgent chatter is rationed per lap by the verbosity setting, with a minimum gap between calls.
- A newer call with the same key replaces an unsaid older one; anything stale is dropped silently.
- On a qualifying push lap only safety and traffic get through.
- Pressing push-to-talk cuts the engineer off and holds the queue until the driver has finished.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from box_box.config import Settings
from box_box.engineer.radio import Priority, RadioCall, Verbosity
from box_box.state.model import World
from box_box.telemetry.packets import DriverStatus

BUDGET_PER_LAP = {Verbosity.QUIET: 2, Verbosity.NORMAL: 4, Verbosity.CHATTY: 7}
MIN_SPACING_S = {Verbosity.QUIET: 8.0, Verbosity.NORMAL: 4.0, Verbosity.CHATTY: 2.5}
STRAIGHT_HOLD_S = 0.35  # how long the car must have been "on a straight" before we start talking
PUSH_LAP_ALLOWED = {"traffic", "flags"}
LINE_WINDOW_MS = 6000  # just after the line on back-to-back push laps: time for the lap time and position

_seq = itertools.count()


@dataclass(order=True)
class _Queued:
    sort_key: tuple[int, int]
    call: RadioCall = field(compare=False)
    deadline: float = field(compare=False)  # monotonic time after which it's stale
    force_at: float | None = field(compare=False)  # monotonic time after which we stop waiting for a straight


@dataclass
class Decision:
    call: RadioCall
    interrupt: bool  # cut off whatever is playing


class RadioDirector:
    def __init__(self, settings: Callable[[], Settings], clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        self._queue: list[_Queued] = []
        self._last_busy = -1e9
        self._last_spoken_at = -1e9
        self._spoken_this_lap = 0
        self._lap = -1
        self._hold_until = 0.0
        self._ptt_down = False
        self.muted_until = 0.0
        self.dropped = 0

    # ---- inputs -----------------------------------------------------------------------------------

    def offer(self, calls: list[RadioCall]) -> None:
        if not calls:
            return
        s = self._settings()
        verbosity = Verbosity.parse(s.engineer.verbosity)
        now = self._clock()
        for call in calls:
            if call.source == "engineer":
                if call.min_verbosity > verbosity:
                    continue
                if call.category != "general" and not getattr(s.callouts, call.category, True):
                    continue
            ttl = max(0.5, call.expires_at - call.created_at)
            force_at = None
            if call.priority == Priority.SAFETY:
                force_at = now
            elif call.source == "reply":
                force_at = now + 1.5
            elif call.priority == Priority.STRATEGY:
                force_at = now + min(ttl * 0.5, 4.0)
            # A newer call with the same key replaces the queued one.
            self._queue = [q for q in self._queue if q.call.key != call.key]
            self._queue.append(_Queued((int(call.priority), next(_seq)), call, now + ttl, force_at))
        self._queue.sort()

    def on_telemetry(self, world: World) -> None:
        """Track whether the driver is busy (braking, turning, lifting) for the straights-only rule."""
        me = world.player
        if me is None or me.telemetry is None or me.lap is None:
            return
        if me.lap.lap_num != self._lap:
            self._lap = me.lap.lap_num
            self._spoken_this_lap = 0
        t = me.telemetry
        busy = t.brake > 0.05 or abs(t.steer) > 0.15 or t.throttle < 0.85
        if busy and me.on_track and me.speed_kph > 60:
            self._last_busy = self._clock()

    def on_ptt(self, pressed: bool) -> None:
        self._ptt_down = pressed
        now = self._clock()
        if pressed:
            self._hold_until = now + 60.0  # until released
        else:
            self._hold_until = now + 1.0  # give the reply a moment to start

    def release_hold(self) -> None:
        """The reply to the driver is ready: stop holding the queue."""
        if not self._ptt_down:
            self._hold_until = 0.0

    def mute(self, seconds: float) -> None:
        self.muted_until = self._clock() + seconds

    def clear(self) -> None:
        self._queue.clear()

    # ---- decisions --------------------------------------------------------------------------------

    def pending(self) -> list[RadioCall]:
        return [q.call for q in self._queue]

    def poll(self, world: World | None, speaking: RadioCall | None) -> Decision | None:
        """Return the next call to say now, or None. `speaking` is what's playing, if anything."""
        now = self._clock()
        before = len(self._queue)
        self._queue = [q for q in self._queue if q.deadline >= now]
        self.dropped += before - len(self._queue)
        if not self._queue:
            return None
        s = self._settings()
        verbosity = Verbosity.parse(s.engineer.verbosity)
        on_straight = self._driver_free(world, s, now)
        me = world.player if world is not None else None
        push_lap = bool(
            s.callouts.quiet_on_push_laps
            and world is not None
            and world.is_qualifying
            and me is not None
            and me.driver_status == DriverStatus.FLYING_LAP
        )
        just_crossed = bool(push_lap and me and me.lap and me.lap.current_lap_ms < LINE_WINDOW_MS)

        for q in list(self._queue):
            call = q.call
            safety = call.priority == Priority.SAFETY
            reply = call.source == "reply"
            # While something's playing, only a safety call may cut in (and never over another safety call).
            if speaking is not None and not (safety and speaking.priority != Priority.SAFETY):
                continue
            if not safety and not reply:
                if now < self._hold_until or now < self.muted_until:
                    continue
                if push_lap and call.category not in PUSH_LAP_ALLOWED:
                    line_call = call.category == "lap_feedback" or call.key == "quali_pos"
                    if not (just_crossed and line_call):
                        continue
            if not safety and not reply and call.priority >= Priority.INFO:
                if self._spoken_this_lap >= BUDGET_PER_LAP[verbosity]:
                    continue
                if now - self._last_spoken_at < MIN_SPACING_S[verbosity]:
                    continue
            if not safety and not on_straight and (q.force_at is None or now < q.force_at):
                continue
            self._queue.remove(q)
            if call.source == "engineer" and not safety:
                self._spoken_this_lap += 1
            self._last_spoken_at = now
            return Decision(call, interrupt=speaking is not None)
        return None

    def _driver_free(self, world: World | None, s: Settings, now: float) -> bool:
        if not s.callouts.straights_only or world is None:
            return True
        me = world.player
        if me is None or me.lap is None or me.telemetry is None or not me.on_track or me.speed_kph < 60:
            return True  # garage, pit lane, menus, parked: no workload
        return now - self._last_busy >= STRAIGHT_HOLD_S
