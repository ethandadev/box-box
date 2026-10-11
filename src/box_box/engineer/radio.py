"""What detectors hand to the radio director: a message, how urgent it is, and how long it stays true."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import IntEnum


class Priority(IntEnum):
    SAFETY = 0  # interrupts anything, ignores the workload gate (flags, car stopped, box now)
    STRATEGY = 1  # pit calls, traffic on a push lap
    INFO = 2  # gaps, tyres, lap times
    COLOUR = 3  # encouragement, banter


@dataclass(frozen=True, slots=True)
class RadioCall:
    key: str  # de-dup key: a newer call with the same key replaces an unsaid older one
    priority: Priority
    text: str
    created_at: float  # session time
    expires_at: float  # session time after which saying it would be stale

    def stale(self, now: float) -> bool:
        return now > self.expires_at


def spoken_seconds(seconds: float) -> str:
    """Gap the way it's said on the radio: whole seconds when it's big, tenths when it's close."""
    if seconds >= 3:
        n = round(seconds)
        return "one second" if n == 1 else f"{n} seconds"
    return f"{seconds:.1f}"


class Phrasebook:
    """Picks among variations so the same situation doesn't sound canned, without repeating back-to-back."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._last: dict[str, int] = {}

    def pick(self, key: str, options: list[str], **fmt: object) -> str:
        i = self._rng.randrange(len(options))
        if len(options) > 1 and i == self._last.get(key):
            i = (i + 1) % len(options)
        self._last[key] = i
        return options[i].format(**fmt)
