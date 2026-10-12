"""Turn radio text into what the TTS should actually pronounce.

"1:26.7"  -> "one twenty-six seven"      (lap times, the way engineers read them)
"P4"      -> "P four"
"1.2"     -> "one point two"             (gaps)
"+0.3"    -> "plus zero point three"
"FP1"     -> "F P one"
"""

from __future__ import annotations

import re

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]  # fmt: skip
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return TENS[tens] + ("-" + ONES[ones] if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return ONES[hundreds] + " hundred" + (" and " + number_words(rest) if rest else "")
    thousands, rest = divmod(n, 1000)
    return number_words(thousands) + " thousand" + (" " + number_words(rest) if rest else "")


def _seconds_part(sec: str) -> str:
    """'26.7' -> 'twenty-six seven', '05.3' -> 'oh five three'."""
    whole, _, frac = sec.partition(".")
    w = int(whole)
    words = ("oh " + ONES[w]) if whole.startswith("0") and len(whole) == 2 else number_words(w)
    if frac:
        words += " " + " ".join(ONES[int(d)] for d in frac)
    return words


def _lap_time(m: re.Match[str]) -> str:
    minutes, seconds = m.group(1), m.group(2)
    return f"{number_words(int(minutes))} {_seconds_part(seconds)}"


def _decimal(m: re.Match[str]) -> str:
    sign, whole, frac = m.group(1) or "", m.group(2), m.group(3)
    prefix = "plus " if sign == "+" else "minus " if sign == "-" else ""
    digits = " ".join(ONES[int(d)] for d in frac)
    if int(whole) == 0:
        return prefix + "point " + digits  # engineers say "point eight", never "zero point eight"
    return prefix + number_words(int(whole)) + " point " + digits


def for_speech(text: str) -> str:
    t = text.replace("...", ",")  # long pauses sound like the voice is stuck on a word
    t = re.sub(r"\b(\d{1,2}):(\d{2}(?:\.\d+)?)\b", _lap_time, t)
    t = re.sub(r"\bFP(\d)\b", lambda m: "F P " + ONES[int(m.group(1))], t)
    t = re.sub(r"\bP(\d{1,2})\b", lambda m: "P " + number_words(int(m.group(1))), t)
    t = re.sub(r"\bQ(\d)\b", lambda m: "Q " + ONES[int(m.group(1))], t)
    t = re.sub(r"(?<![\w.])([+-])?(\d+)\.(\d+)\b", _decimal, t)
    t = re.sub(r"\b(\d+)\b", lambda m: number_words(int(m.group(1))) if len(m.group(1)) <= 6 else m.group(1), t)
    t = t.replace("%", " percent").replace("°C", " degrees").replace("°F", " degrees").replace("°", " degrees")
    t = t.replace("VSC", "V S C").replace("DRS", "D R S").replace("ERS", "E R S").replace("DNF", "D N F")
    return re.sub(r"\s+", " ", t).strip()
