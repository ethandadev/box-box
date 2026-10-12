"""Turn radio text into what the TTS should actually pronounce.

"1:26.7"  -> "one twenty-six seven"      (lap times, the way engineers read them)
"P4"      -> "P four"
"1.2"     -> "one point two"             (gaps)
"+0.3"    -> "plus zero point three"
"FP1"     -> "F P one"
"5 km/h"  -> "five k p h"                (never "slash")
"T14"     -> "turn fourteen"
"2.6s"    -> "two point six seconds"
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
    if w == 0 and not frac:
        return "flat"  # "10:00" is "ten flat", not "ten oh zero"
    words = ("oh " + (ONES[w] if w else "oh")) if whole.startswith("0") and len(whole) == 2 else number_words(w)
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


def _symbols(t: str) -> str:
    """Units, slashes and dashes, written the way they're said (the voices read "/" as "slash")."""
    t = re.sub(r"\b50\s*/\s*50\b", "fifty-fifty", t)
    t = re.sub(r"(?<![\w.])(P?\d{1,3})\s*[-\u2013]\s*(P?\d{1,3})(?!\w|\.\d)", r"\1 to \2", t)  # "turns 1-3"
    t = re.sub(r"(\d:\d{2}(?:\.\d+)?)s\b", r"\1", t)  # "1:30.2s" is just a lap time
    t = re.sub(r"\s*km\s*/\s*h\b|\bkph\b", " k p h", t, flags=re.IGNORECASE)
    t = re.sub(r"\bmph\b", "m p h", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s?kg\b", r"\1 kilos", t)
    t = re.sub(r"(?<![\d:.])(\d{1,3}(?:\.\d+)?)s\b", r"\1 seconds", t)  # "2.6s"; never "1990s"
    t = re.sub(r"\bT(\d{1,2})\b", r"turn \1", t)
    t = re.sub(r"\b(\d+)\s*/\s*(\d+)\b", r"\1 of \2", t)  # "lap 3/5" -> "lap 3 of 5"
    t = re.sub(r"(?<=[A-Za-z])\s*/\s*(?=[A-Za-z])", " or ", t)  # "softs/mediums"
    t = t.replace("/", " ")
    t = re.sub(r"\s+[-\u2013\u2014]+\s+|[\u2013\u2014]", ", ", t)  # spoken dashes are a pause
    return t.replace("&", " and ").replace("~", "about ")


def for_speech(text: str) -> str:
    t = text.replace("...", ",")  # long pauses sound like the voice is stuck on a word
    t = _symbols(t)
    t = re.sub(r"\b(\d{1,2}):(\d{2}(?:\.\d+)?)\b", _lap_time, t)
    t = re.sub(r"\bFP(\d)\b", lambda m: "F P " + ONES[int(m.group(1))], t)
    t = re.sub(r"\bP(\d{1,2})\b", lambda m: "P " + number_words(int(m.group(1))), t)
    t = re.sub(r"\bSQ(\d)\b", lambda m: "S Q " + ONES[int(m.group(1))], t)
    t = re.sub(r"\bQ(\d)\b", lambda m: "Q " + ONES[int(m.group(1))], t)
    t = re.sub(r"(?<![\w.])([+-])?(\d+)\.(\d+)\b", _decimal, t)
    t = re.sub(r"(?<![\w.])\+(\d+)\b", r"plus \1", t)  # "+1 lap"
    t = re.sub(r"(?<![\w.])-(\d+)\b", r"minus \1", t)  # "-3 places"
    t = re.sub(r"\b(\d+)\b", lambda m: number_words(int(m.group(1))) if len(m.group(1)) <= 6 else m.group(1), t)
    t = t.replace("%", " percent").replace("°C", " degrees").replace("°F", " degrees").replace("°", " degrees")
    for acronym in ("VSC", "DRS", "ERS", "DNF", "MFD"):
        t = re.sub(rf"\b{acronym}\b", " ".join(acronym), t)
    return re.sub(r"\s+", " ", t).strip()
