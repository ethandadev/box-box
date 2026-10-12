"""What detectors hand to the radio director: a message, how urgent it is, and how long it stays true."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from enum import IntEnum

from box_box.engineer.phrases import PHRASES


class Priority(IntEnum):
    SAFETY = 0  # interrupts anything, ignores the workload gate and budget (flags, puncture, box now)
    STRATEGY = 1  # pit calls, traffic, race-defining info
    INFO = 2  # gaps, lap times, tyres
    COLOUR = 3  # encouragement, banter


class Verbosity(IntEnum):
    QUIET = 0
    NORMAL = 1
    CHATTY = 2

    @classmethod
    def parse(cls, value: str) -> Verbosity:
        return {"quiet": cls.QUIET, "normal": cls.NORMAL, "chatty": cls.CHATTY}.get(value, cls.NORMAL)


_ids = itertools.count(1)


@dataclass(frozen=True, slots=True)
class RadioCall:
    key: str  # de-dup key: a newer call with the same key replaces an unsaid older one
    priority: Priority
    text: str
    created_at: float  # session time
    expires_at: float  # session time after which saying it would be stale
    category: str = "general"  # matches a CalloutSettings toggle ("traffic", "tyres"...); "general" always on
    min_verbosity: Verbosity = Verbosity.QUIET  # only said at this verbosity or chattier
    source: str = "engineer"  # "engineer" (proactive), "reply" (answering the driver), "system"
    id: int = field(default_factory=lambda: next(_ids))

    def stale(self, now: float) -> bool:
        return now > self.expires_at


# ---- how numbers are said on the radio -----------------------------------------------------------


def spoken_seconds(seconds: float) -> str:
    """Gap the way it's said on the radio: whole seconds when it's big, tenths when it's close."""
    if seconds >= 10:
        return f"{round(seconds)} seconds"
    if seconds >= 3:
        n = round(seconds)
        return "one second" if n == 1 else f"{n} seconds"
    return f"{seconds:.1f}"


def gap(seconds: float) -> str:
    """'1.2', '4.5', '12 seconds'. Engineers drop the unit for gaps under ten seconds."""
    seconds = abs(seconds)
    if seconds >= 10:
        return f"{round(seconds)} seconds"
    return f"{seconds:.1f}"


def tenths(seconds: float) -> str:
    """A small delta the way engineers say it: 'a tenth', 'three tenths', 'half a second', '1.4 seconds'."""
    s = abs(seconds)
    t = round(s * 10)
    if t == 0:
        hundredths = round(s * 100)
        return "a couple of hundredths" if hundredths <= 3 else f"{hundredths} hundredths"
    if t == 1:
        return "a tenth"
    if t == 5:
        return "half a second"
    if t < 10:
        words = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        return f"{words[t]} tenths"
    return f"{s:.1f} seconds"


def lap_time(ms: int) -> str:
    """1:26.7 style, for display; the TTS normaliser turns it into 'one twenty-six seven'."""
    if ms <= 0:
        return "no time"
    total = ms / 1000.0
    minutes = int(total // 60)
    seconds = total - minutes * 60
    return f"{minutes}:{seconds:04.1f}" if minutes else f"{seconds:.1f}"


def ordinal_position(p: int) -> str:
    return f"P{p}"


# Sentence-opening words that read naturally in lower case after "Okay, " (everything else, like
# driver names, "P4" or numbers, keeps its capital).
LOWERCASE_OK = {
    "gap",
    "tyres",
    "fuel",
    "that",
    "you",
    "your",
    "you're",
    "we",
    "we're",
    "it",
    "it's",
    "the",
    "there",
    "good",
    "nice",
    "pace",
    "battery",
    "track",
    "rain",
    "box",
    "stay",
    "brakes",
    "front",
    "car",
    "radar",
    "inside",
    "taking",
    "wear",
    "deg",
    "these",
    "still",
    "most",
    "whole",
    "pit",
    "just",
    "tyre",
    "this",
}


class Phrasebook:
    """Picks among variations so the same situation doesn't sound canned, without repeating back-to-back.

    Phrases live in `phrases.PHRASES[key][personality]`; any personality missing a key falls back to calm.
    """

    def __init__(self, personality: str = "calm", seed: int | None = None) -> None:
        self.personality = personality
        self._rng = random.Random(seed)
        self._last: dict[str, int] = {}

    def __call__(self, key: str, **fmt: object) -> str:
        table = PHRASES[key]
        options = table.get(self.personality) or table["calm"]
        return self.pick(f"{self.personality}:{key}", options, **fmt)

    def pick(self, key: str, options: list[str], **fmt: object) -> str:
        i = self._rng.randrange(len(options))
        if len(options) > 1 and i == self._last.get(key):
            i = (i + 1) % len(options)
        self._last[key] = i
        return options[i].format(**fmt)

    def chance(self, p: float) -> bool:
        return self._rng.random() < p

    def lead(self, text: str, me: str = "", p: float = 0.3) -> str:
        """Sometimes open a call the way engineers do ("Okay, ...", "And Ethan, ..."), so not every
        transmission starts cold. Proper names and numbers keep their capitals."""
        if not text or not self.chance(p) or text.startswith(("Okay", "And ", "Just for info")):
            return text
        opener = self.pick("lead", ["Okay, ", "And ", "Just for info, "])
        first, _, rest = text.partition(" ")
        if first.lower().strip(",.") in LOWERCASE_OK:
            first = first[0].lower() + first[1:]
        return opener + first + (" " + rest if rest else "")
