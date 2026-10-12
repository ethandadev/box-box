"""Small, human imperfections in how the engineer talks.

Real race engineers don't read from a script. They say "uh", restart a sentence, correct themselves
("this is another new medium... sorry, another new hard"), say "copy, copy", and call you "mate".
Done rarely and in the right places, this is what stops a radio voice sounding like a machine.

Rules that keep it safe:
- Never on safety calls (flags, punctures, box-now under a safety car): those stay crisp.
- At most one slip per transmission, and a cooldown between slips so it never becomes a tic.
- A self-correction always ends on the true value: "1.3... sorry, 1.2" only when 1.2 is the real gap.
- Off, subtle (default) or very human, from Settings > Engineer.
"""

from __future__ import annotations

import random
import re

# Chance that a transmission gets a slip, and the minimum number of clean calls in between.
RATES = {"off": 0.0, "subtle": 0.1, "human": 0.22}
COOLDOWN = {"off": 99, "subtle": 4, "human": 2}
NUMBER_SLIP_EVERY = 12  # a misspoken number at most once in this many transmissions

# Calls where a misspoken number is harmless and immediately corrected.
NUMBER_SLIP_KEYS = {"gaps", "pit_done", "rival", "time_left", "laps_to_go", "tyre_plan"}  # never a lap time
COMPOUND_WORDS = ["softs", "mediums", "hards"]

_DECIMAL = re.compile(r"(?<![\d:.])(\d+)\.(\d)(?!\d)")
_LAP_TIME = re.compile(r"\b(\d):(\d{2})\.(\d)\b")


class Humanizer:
    def __init__(self, rng: random.Random | None = None, proper: set[str] | None = None) -> None:
        self._rng = rng or random.Random()
        self.proper = proper if proper is not None else set()  # names that keep their capital
        self._since_slip = 99
        self._calls = 0
        self._last_number_slip = -99

    def apply(self, text: str, level: str, *, key: str = "", safety: bool = False, reply: bool = False) -> str:
        """Maybe add one natural imperfection. `reply` limits it to spoken-reply habits (no number slips)."""
        rate = RATES.get(level, 0.0)
        if safety or not rate or not text:
            return text
        self._since_slip += 1
        self._calls += 1
        if self._since_slip <= COOLDOWN.get(level, 3) or self._rng.random() >= rate:
            return text
        # "uh"/"er" and trailing-off restarts read well but a synthetic voice can't say them convincingly,
        # so the slips are the ones that survive text-to-speech: openers, "copy, copy", "mate", corrections.
        options = [self._okay_so, self._mate]
        if reply:
            options = [self._yeah, self._copy_copy, self._mate]
        else:
            if key.split(":")[0] in NUMBER_SLIP_KEYS and self._calls - self._last_number_slip >= NUMBER_SLIP_EVERY:
                options.append(self._number_slip)
            if key == "pit_done":
                options.append(self._compound_slip)
        self._rng.shuffle(options)
        for slip in options:
            out = slip(text)
            if out and out != text:
                self._since_slip = 0
                if slip == self._number_slip:
                    self._last_number_slip = self._calls
                return out
        return text

    # ---- the slips ---------------------------------------------------------------------------------

    def _okay_so(self, text: str) -> str | None:
        """'Okay, so gap to Leclerc...': a spoken lead-in, without a stumble the voice can't pull off."""
        if text.startswith(("Okay", "So", "And", "Just for info", "Yeah")):
            return None
        first, _, rest = text.partition(" ")
        if not rest or not first[:1].isalpha():
            return None
        from box_box.engineer.radio import soften

        return self._rng.choice(["Okay, so ", "So, "]) + soften(first, self.proper) + " " + rest

    def _mate(self, text: str) -> str | None:
        """'Good lap, mate.' on a short, friendly sentence."""
        if "mate" in text or len(text) > 70 or not text.endswith("."):
            return None
        if text.count(".") > 2 or any(ch.isdigit() for ch in text[-6:]):
            return None  # don't hang 'mate' off a number
        if re.search(r",\s+[A-Z][\w'-]*\.$", text):
            return None  # already ends on his name: "That's fastest lap, Ethan." not "..., Ethan, mate."
        return text[:-1] + ", mate."

    def _yeah(self, text: str) -> str | None:
        if text.startswith(("Yeah", "Copy", "Okay", "No ", "Negative", "Affirm")):
            return None
        from box_box.engineer.radio import soften

        first, _, rest = text.partition(" ")
        return "Yeah, " + soften(first, self.proper) + (" " + rest if rest else "")

    def _copy_copy(self, text: str) -> str | None:
        if text.startswith("Copy.") or text.startswith("Copy,"):
            return "Copy, copy" + text[4:]
        return None

    def _number_slip(self, text: str) -> str | None:
        """Misread a number by one digit and correct it straight away; the correction is the real value."""
        m = _LAP_TIME.search(text)
        if m:
            wrong_tenth = (int(m.group(3)) + self._rng.choice([1, -1])) % 10
            wrong = f"{m.group(1)}:{m.group(2)}.{wrong_tenth}"
            return text[: m.start()] + f"{wrong}, sorry, {m.group(0)}" + text[m.end() :]
        m = _DECIMAL.search(text)
        if m:
            whole, tenth = int(m.group(1)), int(m.group(2))
            wrong_tenth = tenth + self._rng.choice([1, -1])
            if not 0 <= wrong_tenth <= 9:
                wrong_tenth = tenth - 1 if tenth > 0 else tenth + 1
            wrong = f"{whole}.{wrong_tenth}"
            return text[: m.start()] + f"{wrong}, sorry, {m.group(0)}" + text[m.end() :]
        return None

    def _compound_slip(self, text: str) -> str | None:
        """'New mediums... sorry, new hards.' as in the real thing."""
        for word in COMPOUND_WORDS:
            m = re.search(rf"\b[Nn]ew {word}\b", text)
            if m:
                wrong = self._rng.choice([w for w in COMPOUND_WORDS if w != word])
                said = m.group(0)
                return text[: m.start()] + f"{said[:3]} {wrong}, sorry, new {word}" + text[m.end() :]
        return None
