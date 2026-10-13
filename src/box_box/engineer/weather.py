"""Weather: rain on the radar, rain starting and stopping, and being on the wrong tyre for the conditions."""

from __future__ import annotations

from box_box.engineer.base import Context, Detector
from box_box.engineer.radio import Priority, RadioCall, Verbosity
from box_box.state.model import LapCompleted

RAIN = 3  # weather >= 3 is rain (light, heavy, storm)
INTENSITY = {3: "light rain", 4: "heavy rain", 5: "it's a storm"}
SLICKS = {16, 17, 18, 19, 20, 21, 22}
WETS = {7, 8, 15}


class WeatherDetector(Detector):
    name = "weather"

    def reset(self) -> None:
        self._weather: int | None = None
        self._announced_rain_at: float | None = None  # session time we said rain would arrive
        self._dry_laps_on_wets = 0
        self._last_tyre_call_lap = -10

    def on_rewind(self) -> None:
        pass  # weather calls don't depend on the future

    def check(self, ctx: Context) -> list[RadioCall]:
        world = ctx.world
        s = world.session
        me = world.player
        if s is None or me is None or me.lap is None or world.is_time_trial:
            return []
        out: list[RadioCall] = []

        prev, self._weather = self._weather, s.weather
        if prev is not None and prev != s.weather:
            if s.weather >= RAIN > prev:
                out.append(
                    ctx.call(
                        "rain",
                        Priority.STRATEGY,
                        ctx.say("rain_started", intensity=INTENSITY.get(s.weather, "rain")),
                        ttl=20,
                        category="weather",
                    )
                )
                self._announced_rain_at = None
            elif prev >= RAIN > s.weather:
                out.append(ctx.call("rain", Priority.INFO, ctx.say("rain_stopping"), ttl=20, category="weather"))

        if s.weather < RAIN:
            forecast = [f for f in s.forecast if f.session_type == s.session_type and f.time_offset_min > 0]
            coming = next(
                (
                    f
                    for f in sorted(forecast, key=lambda f: f.time_offset_min)
                    if f.weather >= RAIN and f.rain_percentage >= 40
                ),
                None,
            )
            if coming is not None:
                arrive = ctx.now + coming.time_offset_min * 60
                # The forecast comes in coarse slots (0/5/10/15/30/45/60 min), so the same rain drifts later
                # by minutes between updates: only speak again if it's now coming sooner than we said.
                if (
                    self._announced_rain_at is None
                    or self._announced_rain_at - arrive > 300
                    or ctx.now > self._announced_rain_at
                ):
                    self._announced_rain_at = arrive
                    text = ctx.say("rain_coming", minutes=coming.time_offset_min, pct=coming.rain_percentage)
                    out.append(ctx.call("rain_forecast", Priority.INFO, text, ttl=60, category="weather"))

        for change in ctx.changes:
            if isinstance(change, LapCompleted) and change.car_index == me.index and world.is_race:
                out += self._tyre_choice(ctx, s.weather, me.compound, me.lap.lap_num)
        return out

    def _tyre_choice(self, ctx: Context, weather: int, compound: int | None, lap: int) -> list[RadioCall]:
        if compound is None or lap - self._last_tyre_call_lap < 3:
            return []
        if weather >= 4 and compound in SLICKS:
            self._last_tyre_call_lap = lap
            return [
                ctx.call(
                    "tyre_choice",
                    Priority.STRATEGY,
                    ctx.say("wrong_tyres_wet", tyre="wets"),
                    ttl=30,
                    category="weather",
                )
            ]
        if weather <= 1 and compound in WETS:
            self._dry_laps_on_wets += 1
            if self._dry_laps_on_wets >= 2:
                self._last_tyre_call_lap = lap
                self._dry_laps_on_wets = 0
                return [
                    ctx.call(
                        "tyre_choice",
                        Priority.INFO,
                        ctx.say("wrong_tyres_dry"),
                        ttl=30,
                        category="weather",
                        verbosity=Verbosity.NORMAL,
                    )
                ]
        else:
            self._dry_laps_on_wets = 0
        return []
