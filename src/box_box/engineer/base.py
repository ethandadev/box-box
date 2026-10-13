"""The detector contract: each tick, look at the world and return the radio calls worth making."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from box_box.config import Settings
from box_box.engineer.radio import Phrasebook, Priority, RadioCall, Verbosity
from box_box.state.model import Change, SessionChanged, World
from box_box.telemetry.packets import Event

log = logging.getLogger(__name__)


# Calls where a real engineer would often open with "Okay," / "And," / "Just for info," rather than
# launching straight in. Not lap times (those are said bare, the instant the lap ends) or warnings.
CONVERSATIONAL_KEYS = {"gaps", "pit_window", "rival", "tyre_plan", "sc_window", "grid_tyres", "pit_done", "tyres"}


# Calls that must sound exactly as written, whatever the natural-speech setting.
CRISP_KEYS = {
    "box", "last_chance", "quali_pos", "wing", "puncture", "sc", "red", "finish", "last_lap", "greet", "impact",
    "traffic", "blue", "penalty", "lap_deleted", "yellow", "warning",
}  # fmt: skip


@dataclass
class Context:
    world: World
    changes: list[Change]
    events: list[Event]
    settings: Settings
    say: Phrasebook

    @property
    def now(self) -> float:
        return self.world.session_time

    @property
    def me(self) -> str:
        return self.settings.driver.name or "mate"

    @staticmethod
    def name(car, fallback: str) -> str:  # type: ignore[no-untyped-def]
        """A car's radio name, or e.g. "the car ahead" before the game has told us who it is."""
        if car is not None and car.participant and car.participant.name:
            return car.name
        return fallback

    def call(
        self,
        key: str,
        priority: Priority,
        text: str,
        *,
        ttl: float,
        category: str = "general",
        verbosity: Verbosity = Verbosity.QUIET,
    ) -> RadioCall:
        # Names keep their capitals when an opener goes in front ("Okay, so Russell is through").
        self.say.proper.update(c.name for c in self.world.cars if c.participant and c.participant.name)
        if priority == Priority.INFO and key.split(":")[0] in CONVERSATIONAL_KEYS:
            text = self.say.lead(text, me=self.me)
        crisp = priority == Priority.SAFETY or key.split(":")[0] in CRISP_KEYS
        text = self.say.human.apply(text, self.settings.engineer.natural_speech, key=key, safety=crisp)
        text = self.say.human.emphasize(text, self.settings.engineer.natural_speech, key=key, me=self.me)
        return RadioCall(
            key=key,
            priority=priority,
            text=text,
            created_at=self.now,
            expires_at=self.now + ttl,
            category=category,
            min_verbosity=verbosity,
        )


class Detector:
    """Base class. Subclasses implement `check`; `reset` clears memory on a new session or flashback."""

    name = "detector"

    def __init__(self) -> None:
        self._timeline = -1
        self._failures = 0
        self.reset()

    def reset(self) -> None:  # noqa: B027 - optional hook
        pass

    def on_rewind(self) -> None:
        """Flashback: by default forget everything (calls about the abandoned future are wrong)."""
        self.reset()

    def update(self, ctx: Context) -> list[RadioCall]:
        if any(isinstance(c, SessionChanged) for c in ctx.changes):
            self.reset()
            self._timeline = ctx.world.timeline
        elif ctx.world.timeline != self._timeline:
            if self._timeline != -1:
                self.on_rewind()
            self._timeline = ctx.world.timeline
        try:
            return self.check(ctx)
        except Exception:
            # One broken detector must never take the radio down. Log the first few, then go quiet.
            self._failures += 1
            if self._failures <= 3:
                log.exception("detector %s failed", self.name)
            return []

    def check(self, ctx: Context) -> list[RadioCall]:
        raise NotImplementedError
