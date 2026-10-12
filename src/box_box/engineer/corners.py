"""Corner-by-corner coaching: where on the lap the time went, and why.

Every lap we record, for each corner of the circuit: the time through it (between the midpoints to the
neighbouring corners), the entry speed, the minimum (apex) speed and the exit speed. Compared with the
driver's best lap, that turns "lost it in sector 2" into "two tenths at Copse, apex speed 6 km/h down".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from box_box.state import tracks
from box_box.state.model import CarState, World

ENTRY_M = 110.0  # before the corner marker: where braking has started
APEX_WINDOW = (-60.0, 160.0)  # where the minimum speed is found (markers sit just before the apex)
EXIT_M = 190.0  # after the marker: the exit
MIN_LOSS_S = 0.08  # smaller than this is noise


@dataclass
class CornerSample:
    entry: float | None = None
    apex: float = 999.0
    exit: float | None = None
    t_in: int | None = None  # current_lap_ms when entering the corner's segment
    t_out: int | None = None

    @property
    def time_ms(self) -> int | None:
        return self.t_out - self.t_in if self.t_in is not None and self.t_out is not None else None


@dataclass
class LapTrace:
    corners: dict[str, CornerSample] = field(default_factory=dict)
    last_distance: float = -1.0


@dataclass
class Finding:
    corner: tracks.Corner
    loss_s: float
    reason: str  # "apex", "entry", "exit" or ""
    speed_diff: float  # km/h, positive = slower than the best lap

    def say(self, units: str = "metric") -> str:
        from box_box.engineer.radio import tenths

        where = self.corner.label
        speed = abs(self.speed_diff)
        unit = "km/h"
        if units == "imperial":
            speed, unit = speed * 0.621, "mph"
        detail = {
            "apex": f", apex speed {round(speed)} {unit} down",
            "entry": ", you're braking early",
            "exit": ", late on the power on the exit",
        }.get(self.reason, "")
        return f"Lost {tenths(self.loss_s)} at {where}{detail}."


class CornerCoach:
    """Feed it every telemetry tick; ask it about a lap once the lap is done."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._track: tracks.Track | None = None
        self._track_id: int | None = None
        self._segments: list[tuple[tracks.Corner, float, float, float]] = []  # corner, seg start, marker, seg end
        self._lap_num = -1
        self._trace = LapTrace()
        self.laps: dict[int, LapTrace] = {}
        self.best: tuple[int, LapTrace] | None = None  # (lap ms, trace)
        self.last_finding: Finding | None = None

    def rewind(self) -> None:
        self._trace = LapTrace()
        self._lap_num = -1

    def _layout(self, world: World) -> bool:
        s = world.session
        if s is None:
            return False
        if s.track_id != self._track_id:
            self.reset()
            self._track_id = s.track_id
            self._track = tracks.get(s.track_id)
            if self._track and self._track.corners and world.track_length > 0:
                pts = self._track.corners_on(world.track_length)
                length = world.track_length
                for i, (c, at) in enumerate(pts):
                    prev_at = pts[i - 1][1] if i else pts[-1][1] - length
                    next_at = pts[i + 1][1] if i + 1 < len(pts) else pts[0][1] + length
                    self._segments.append((c, (prev_at + at) / 2, at, (at + next_at) / 2))
        return bool(self._segments)

    def sample(self, world: World, me: CarState) -> None:
        if me.lap is None or me.telemetry is None or not self._layout(world):
            return
        lap = me.lap
        if lap.lap_num != self._lap_num:
            if self._lap_num >= 0:
                self.laps[self._lap_num] = self._trace
                for old in [k for k in self.laps if k < lap.lap_num - 3]:
                    del self.laps[old]
            self._lap_num, self._trace = lap.lap_num, LapTrace()
        d, v, t = lap.lap_distance, float(me.telemetry.speed_kph), lap.current_lap_ms
        if d < 0 or d < self._trace.last_distance - 50:  # behind the line, or jumped back (flashback)
            return
        self._trace.last_distance = d
        for corner, start, at, end in self._segments:
            if not start - 30 <= d <= end + 30:
                continue
            cs = self._trace.corners.setdefault(corner.turn, CornerSample())
            if cs.t_in is None and d >= start:
                cs.t_in = t
            if d >= end and cs.t_out is None and cs.t_in is not None:
                cs.t_out = t
            if cs.entry is None and d >= at - ENTRY_M:
                cs.entry = v
            if at + APEX_WINDOW[0] <= d <= at + APEX_WINDOW[1]:
                cs.apex = min(cs.apex, v)
            if cs.exit is None and d >= at + EXIT_M:
                cs.exit = v

    def lap_done(self, lap_num: int, lap_ms: int, valid: bool) -> Finding | None:
        """Compare a finished lap with the best one; remember it if it's the new best."""
        trace = self.laps.get(lap_num)
        if trace is None or not valid or lap_ms <= 0 or len(trace.corners) < max(3, len(self._segments) // 2):
            return None
        finding = None
        if self.best is not None and lap_ms > self.best[0]:
            finding = self._compare(trace, self.best[1])
            if finding:
                self.last_finding = finding
        if self.best is None or lap_ms < self.best[0]:
            self.best = (lap_ms, trace)
        return finding

    def _compare(self, cur: LapTrace, best: LapTrace) -> Finding | None:
        worst: Finding | None = None
        for corner, *_ in self._segments:
            a, b = cur.corners.get(corner.turn), best.corners.get(corner.turn)
            if a is None or b is None or a.time_ms is None or b.time_ms is None:
                continue
            loss = (a.time_ms - b.time_ms) / 1000
            if loss < MIN_LOSS_S or (worst and loss <= worst.loss_s):
                continue
            reason, diff = "", 0.0
            if a.apex < 900 and b.apex < 900 and b.apex - a.apex >= 3:
                reason, diff = "apex", b.apex - a.apex
            elif a.entry is not None and b.entry is not None and b.entry - a.entry >= 5:
                reason, diff = "entry", b.entry - a.entry
            elif a.exit is not None and b.exit is not None and b.exit - a.exit >= 4:
                reason, diff = "exit", b.exit - a.exit
            worst = Finding(corner, loss, reason, diff)
        return worst
