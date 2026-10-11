"""2026 power units: overtake mode availability and battery state."""

from __future__ import annotations

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity

BATTERY_LOW_J = 0.4e6  # store tops out at 4 MJ


class EnergyDetector(Detector):
    name = "energy"

    def reset(self) -> None:
        self._overtake_was = False
        self._overtake_called_lap = -1
        self._battery_called_lap = -10

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        me = world.player
        if (
            not world.is_race
            or me is None
            or me.lap is None
            or not world.regulations_2026
            or me.in_pit_lane
            or not me.active
        ):
            return []
        if world.laps_remaining is not None and world.laps_remaining <= 1:
            return []  # final lap: nothing left to manage
        out: list[RadioCall] = []
        t2 = me.telemetry2
        lap = me.lap.lap_num
        if t2 is not None:
            available = t2.overtake_available
            if available and not self._overtake_was and lap != self._overtake_called_lap and lap > 1:
                self._overtake_called_lap = lap
                ahead = world.car_at_position(me.lap.position - 1)
                name = ahead.name if ahead else "the car ahead"
                out.append(
                    ctx.call(
                        "overtake",
                        Priority.INFO,
                        ctx.say("overtake_available", name=name),
                        ttl=6,
                        category="energy",
                        verbosity=Verbosity.NORMAL,
                    )
                )
            self._overtake_was = available
        status = me.status
        if status and 0 < status.ers_store_j < BATTERY_LOW_J and lap - self._battery_called_lap >= 3 and lap > 1:
            self._battery_called_lap = lap
            out.append(
                ctx.call(
                    "battery",
                    Priority.INFO,
                    ctx.say("battery_low"),
                    ttl=15,
                    category="energy",
                    verbosity=Verbosity.NORMAL,
                )
            )
        return out
