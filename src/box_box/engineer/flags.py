"""Flags, safety car, penalties and deleted laps."""

from __future__ import annotations

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity
from box_box.state import tracks

# Penalty types (EA spec appendix).
DRIVE_THROUGH, STOP_GO, TIME_PENALTY, WARNING = 0, 1, 4, 5
LAP_INVALIDATED = {10, 11, 12, 13, 14, 15}

# Infringements explained in a few words for the radio.
INFRINGEMENT_REASON = {
    0: "Blocking.",
    3: "That was for the collision.",
    4: "That was for the contact.",
    5: "You didn't give the place back.",
    6: "You didn't give the places back.",
    7: "Corner cutting.",
    8: "Gaining a position off track.",
    9: "Gaining positions off track.",
    10: "Crossed the pit exit line.",
    11: "Ignoring blue flags.",
    12: "Ignoring yellow flags.",
    17: "Speeding in the pit lane.",
    27: "Running wide and gaining time.",
    28: "Running wide and gaining time.",
    29: "Running wide and gaining time.",
    34: "Jump start.",
    36: "Overtaking under the safety car.",
    37: "Too fast under the safety car.",
    38: "Too fast under the VSC.",
}
TRACK_LIMIT_INFRINGEMENTS = {7, 25, 26, 27, 28, 29}


class FlagsDetector(Detector):
    name = "flags"

    def reset(self) -> None:
        self._sc_status: int | None = None
        self._yellow_since: dict[int, float] = {}
        self._yellow_announced: set[int] = set()
        self._yellow_called: dict[int, float] = {}  # sector -> last call time
        self._blue_called_at = -1e9
        self._warnings = 0
        self._lap_invalid_seen: tuple[int, bool] | None = None

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        me = world.player
        session = world.session
        calls: list[RadioCall] = []
        if session is None or me is None or me.lap is None:
            return calls

        calls += self._safety_car(ctx, session.safety_car_status)
        calls += self._events(ctx)
        if me.on_track:
            calls += self._yellows(ctx)
            calls += self._blue(ctx)
        calls += self._lap_invalid(ctx)
        return calls

    # -- safety car / VSC: Session status transitions, so it works even if an SCAR event is lost --------

    def _safety_car(self, ctx: Context, status: int) -> list[RadioCall]:
        prev, self._sc_status = self._sc_status, status
        if prev is None or prev == status:
            return []
        if status == 1:
            return [ctx.call("sc", Priority.SAFETY, ctx.say("safety_car"), ttl=10, category="flags")]
        if status == 2:
            return [ctx.call("sc", Priority.SAFETY, ctx.say("vsc"), ttl=10, category="flags")]
        if status == 0 and prev in (1, 2) and ctx.world.is_race:
            return [ctx.call("sc", Priority.SAFETY, ctx.say("green_flag"), ttl=6, category="flags")]
        return []

    def _events(self, ctx: Context) -> list[RadioCall]:
        out: list[RadioCall] = []
        me_idx = ctx.world.player_index
        for e in ctx.events:
            if e.code == "RDFL":
                out.append(ctx.call("red", Priority.SAFETY, ctx.say("red_flag"), ttl=15, category="flags"))
            elif e.code == "SCAR" and e.detail.get("event_type") == 1:  # returning
                key = "vsc_ending" if e.detail.get("safety_car_type") == 2 else "safety_car_in"
                out.append(ctx.call("sc", Priority.STRATEGY, ctx.say(key), ttl=20, category="flags"))
            elif e.code == "PENA" and e.vehicle_idx == me_idx:
                call = self._penalty(ctx, e.detail, int(e.value or 0))
                if call:
                    out.append(call)
        return out

    def _penalty(self, ctx: Context, detail: dict, seconds: int) -> RadioCall | None:
        ptype = detail.get("penalty_type")
        infr = detail.get("infringement_type", -1)
        reason = INFRINGEMENT_REASON.get(infr, "")
        if ptype == WARNING:
            self._warnings += 1
            if infr in TRACK_LIMIT_INFRINGEMENTS:
                count = ctx.world.player.lap.corner_cutting_warnings or self._warnings  # type: ignore[union-attr]
                text = ctx.say("warning_track_limits", count=count)
                return ctx.call("warning", Priority.INFO, text, ttl=20, category="flags", verbosity=Verbosity.NORMAL)
            return None
        if ptype == TIME_PENALTY:
            text = ctx.say("penalty_time", seconds=seconds, reason=reason, me=ctx.me).strip()
            return ctx.call("penalty", Priority.STRATEGY, text, ttl=30, category="flags")
        if ptype == DRIVE_THROUGH:
            return ctx.call("penalty", Priority.STRATEGY, ctx.say("penalty_dt"), ttl=30, category="flags")
        if ptype == STOP_GO:
            text = ctx.say("penalty_sg", seconds=seconds or 10)
            return ctx.call("penalty", Priority.STRATEGY, text, ttl=30, category="flags")
        return None

    # -- yellow flags in the marshal zones ahead -------------------------------------------------------

    def _yellows(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        session = world.session
        me = world.player
        assert session and me and me.lap
        zones = session.marshal_zones
        if not zones or world.track_length <= 0:
            return []
        frac = (me.lap.lap_distance % world.track_length) / world.track_length
        out: list[RadioCall] = []
        for i, z in enumerate(zones):
            yellow = z.flag == 3
            if not yellow:
                self._yellow_since.pop(i, None)
                continue
            # The game flickers zone flags; only trust a yellow that's held for a second.
            since = self._yellow_since.setdefault(i, ctx.now)
            if ctx.now - since < 1.0 or i in self._yellow_announced:
                continue
            ahead = (z.start - frac) % 1.0
            if ahead > 0.4 or ahead < 0.01:
                continue  # far away, or the zone we're already in
            if me.speed_kph < 50:
                continue  # we're the stopped car; no point telling the driver
            sector = self._sector_of(world, z.start)
            if ctx.now - self._yellow_called.get(sector, -1e9) < 60:
                self._yellow_announced.add(i)
                continue
            self._yellow_called[sector] = ctx.now
            self._yellow_announced.add(i)
            where = self._where(world, z.start, sector)
            out.append(
                ctx.call(
                    f"yellow:{sector}", Priority.SAFETY, ctx.say("yellow_ahead", where=where), ttl=6, category="flags"
                )
            )
        # A zone that went green can be announced again next time it goes yellow.
        self._yellow_announced &= set(self._yellow_since)
        return out

    @staticmethod
    def _where(world, frac: float, sector: int) -> str:  # type: ignore[no-untyped-def]
        """'turn 4' or 'Copse' when we know the track, else 'sector 2'."""
        track = tracks.get(world.session.track_id) if world.session else None
        if track is not None and track.corners:
            corner = track.corner_after(frac * world.track_length, world.track_length, within=500)
            if corner is not None:
                return corner.label
        return f"sector {sector}"

    @staticmethod
    def _sector_of(world, frac: float) -> int:  # type: ignore[no-untyped-def]
        s = world.session
        dist = frac * world.track_length
        if s and s.sector3_start and dist >= s.sector3_start:
            return 3
        if s and s.sector2_start and dist >= s.sector2_start:
            return 2
        return 1

    def _blue(self, ctx: Context) -> list[RadioCall]:
        me = ctx.world.player
        if not (me and me.status and me.status.fia_flag == 2 and ctx.world.is_race):
            return []
        if ctx.now - self._blue_called_at < 25:
            return []
        self._blue_called_at = ctx.now
        return [ctx.call("blue", Priority.SAFETY, ctx.say("blue_flags", name="the leader"), ttl=5, category="flags")]

    def _lap_invalid(self, ctx: Context) -> list[RadioCall]:
        me = ctx.world.player
        assert me and me.lap
        key = (me.lap.lap_num, me.lap.lap_invalid)
        prev, self._lap_invalid_seen = self._lap_invalid_seen, key
        if prev is None or not me.lap.lap_invalid or prev == key:
            return []
        if prev[0] != me.lap.lap_num:
            return []  # lap rolled over already invalid (e.g. out lap); not news
        if me.driver_status != 1 or ctx.world.is_race:  # only news on a practice/quali push lap
            return []
        return [
            ctx.call(
                "lap_deleted", Priority.STRATEGY, ctx.say("lap_deleted", me=ctx.me), ttl=8, category="lap_feedback"
            )
        ]
