"""What detectors hand to the radio director: a message, how urgent it is, and how long it stays true."""

from __future__ import annotations

import itertools
import random
import re
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
    return f"{max(seconds, 0.1):.1f}"  # side by side is "point one", never "zero point zero"


NO_DATA = [
    "Stand by, no {what} data yet.",
    "[Stand by|Bear with us], [we're having trouble with the sensors|sensor problem this end|we've lost some sensors].",
    "We are checking.",
    "[We are checking|Checking], [stand by|give us a moment].",
    "[Data's dropped out|We've lost the data|Telemetry's dropped] [for a moment|this end], [stand by|give us a sec].",
    "[Stand by|One sec], [we're not seeing the {what} data|no {what} readings here yet].",
]


def no_data(rng: random.Random, what: str = "car") -> str:
    """What the pit wall says when the screens are blank: sometimes plain, sometimes sensors, sometimes
    the famous "We are checking"."""
    text = expand(rng.choice(NO_DATA), rng).format(what=what)
    return text.replace("no car data", "no data").replace("the car data", "the data")


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


def fine_delta(seconds: float) -> str:
    """Qualifying margins: '11 thousandths' when it's that close, otherwise like `tenths`."""
    ms = round(abs(seconds) * 1000)
    if 0 < ms < 100:
        return "one thousandth" if ms == 1 else f"{ms} thousandths"
    return tenths(seconds)


def lap_time(ms: int) -> str:
    """1:26.7 style, for display; the TTS normaliser turns it into 'one twenty-six seven'."""
    if ms <= 0:
        return "no time"
    total = ms / 1000.0
    minutes = int(total // 60)
    seconds = total - minutes * 60
    return f"{minutes}:{seconds:04.1f}" if minutes else f"{seconds:.1f}"


# Words that keep their capital mid-sentence ("Okay, so Russell is through"). Driver names in the session
# are added by the engineer as they become known; the rest is lower-cased after an opener.
TEAMS = {"Mercedes", "Ferrari", "McLaren", "Red", "Williams", "Alpine", "Aston", "Haas", "Sauber", "Audi",
         "Cadillac", "Racing", "Kick", "Visa"}  # fmt: skip


def soften(word: str, proper: set[str] | frozenset[str] = frozenset()) -> str:
    """Lower-case a sentence's first word when something is put in front of it, unless it's a name."""
    from box_box.voice.pronounce import LEXICON

    bare = word.strip(",.!?;:")
    bare = bare[:-2] if bare.endswith("'s") else bare
    if (
        not bare[:1].isupper()
        or bare in proper
        or bare in LEXICON
        or bare in TEAMS
        or bare in {"I", "I'm", "I'll", "I'd", "I've"}
        or any(ch.isupper() or ch.isdigit() for ch in bare[1:])  # P4, DRS, VSC, McLaren
    ):
        return word
    return word[0].lower() + word[1:]


_CHOICE = re.compile(r"\[([^\[\]]*)\]")
RECENT_MEMORY = 8  # a key's last few sentences are avoided, so the same wording almost never comes back


def expand(template: str, rng: random.Random) -> str:
    """Resolve choice brackets: "[Good|Nice|Lovely] lap" picks one; "[, mate|]" may pick nothing."""
    return _CHOICE.sub(lambda m: rng.choice(m.group(1).split("|")), template)


def variants(template: str) -> int:
    """How many different sentences one template can produce."""
    n = 1
    for m in _CHOICE.finditer(template):
        n *= len(m.group(1).split("|"))
    return n


class Phrasebook:
    """Picks among variations so the same situation doesn't sound canned.

    Phrases live in `phrases.PHRASES[key][personality]`; any personality missing a key falls back to calm.
    Templates are dealt like a shuffled deck (every one is used before any comes round again), and each
    can hold choices in brackets, so a key with a handful of templates has dozens of ways to say it. The
    last few sentences said for a key are avoided outright.
    """

    def __init__(self, personality: str = "calm", seed: int | None = None) -> None:
        from box_box.engineer.human import Humanizer

        self.personality = personality
        self._rng = random.Random(seed)
        self._decks: dict[str, list[int]] = {}
        self._last: dict[str, int] = {}
        self._recent: dict[str, list[str]] = {}
        self.proper: set[str] = set()  # names that keep their capital: the driver's, the other cars'
        self.human = Humanizer(random.Random(None if seed is None else seed + 1), self.proper)

    def __call__(self, key: str, **fmt: object) -> str:
        table = PHRASES[key]
        options = table.get(self.personality) or table["calm"]
        return self.pick(f"{self.personality}:{key}", options, **fmt)

    def _deal(self, key: str, n: int) -> int:
        deck = self._decks.get(key)
        if not deck or max(deck) >= n:
            deck = list(range(n))
            self._rng.shuffle(deck)
            if n > 1 and deck[-1] == self._last.get(key):  # never the same template twice in a row
                deck[0], deck[-1] = deck[-1], deck[0]
            self._decks[key] = deck
        i = deck.pop()
        self._last[key] = i
        return i

    def pick(self, key: str, options: list[str], **fmt: object) -> str:
        recent = self._recent.setdefault(key, [])
        text = ""
        for _ in range(6):
            text = expand(options[self._deal(key, len(options))], self._rng).format(**fmt)
            if text not in recent:
                break
        recent.append(text)
        del recent[:-RECENT_MEMORY]
        text = re.sub(r"\s+([,.!?])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()  # an empty choice leaves gaps
        # A sentence-opening placeholder ("{part} damage", "... rain. {intensity}.") still gets a capital.
        text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
        return text[:1].upper() + text[1:]

    @property
    def rng(self) -> random.Random:
        return self._rng

    def chance(self, p: float) -> bool:
        return self._rng.random() < p

    def lead(self, text: str, me: str = "", p: float = 0.3) -> str:
        """Sometimes open a call the way engineers do ("Okay, ...", "And Ethan, ..."), so not every
        transmission starts cold. Proper names and numbers keep their capitals."""
        self._since_lead = getattr(self, "_since_lead", 9) + 1
        if (
            not text
            or self._since_lead <= 2
            or not self.chance(p)
            or text.startswith(("Okay", "And ", "Just for info"))
        ):
            return text
        self._since_lead = 0
        opener = self.pick("lead", ["Okay,", "And", "Just for info,", "[Okay|Right], so", "So,"])
        first, _, rest = text.partition(" ")
        first = soften(first, self.proper | {me})
        return f"{opener} {first}" + (" " + rest if rest else "")
