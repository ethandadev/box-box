"""Natural speech: slips are rare, safe, and always end on the true value."""

import random
import re

from box_box.engineer.human import Humanizer


def run(text: str, level: str = "human", n: int = 400, **kw) -> list[str]:
    h = Humanizer(random.Random(3))
    return [h.apply(text, level, **kw) for _ in range(n)]


def test_off_and_safety_are_untouched() -> None:
    assert set(run("Gap to Leclerc, 1.2.", level="off")) == {"Gap to Leclerc, 1.2."}
    assert set(run("Safety car, safety car.", safety=True)) == {"Safety car, safety car."}


def test_slips_are_rare_and_spaced_out() -> None:
    out = run("Gap to Leclerc, 1.2, closing.", level="subtle", key="gaps")
    changed = [i for i, t in enumerate(out) if t != "Gap to Leclerc, 1.2, closing."]
    assert 0.04 < len(changed) / len(out) < 0.2
    assert all(b - a > 3 for a, b in zip(changed, changed[1:], strict=False))  # cooldown between slips


def test_number_corrections_end_on_the_real_number() -> None:
    for text in run("Gap to Leclerc 1.2, closing.", key="gaps") + run("1:26.8. That's a personal best.", key="lap"):
        m = re.search(r"(\d[\d:.]*\d), sorry, (\d[\d:.]*\d)", text)
        if m:
            assert m.group(2) in ("1.2", "1:26.8") and m.group(1) != m.group(2)


def test_variety_of_human_touches() -> None:
    out = set(run("Gap to Leclerc, 1.2, closing.", key="gaps", n=2000))
    joined = " | ".join(out)
    assert "sorry, 1.2" in joined and ("Okay, so" in joined or "So, gap" in joined)
    assert "uh" not in joined.split() and "..." not in joined  # nothing a synthetic voice stumbles over


def test_reply_habits_and_compound_correction() -> None:
    replies = set(run("Copy. Box this lap.", reply=True, n=1000))
    assert "Copy, copy. Box this lap." in replies
    stops = set(run("2.4 second stop. You're P5. New hards on.", key="pit_done", n=2000))
    assert any(re.search(r"New (softs|mediums), sorry, new hards", t) for t in stops)


def test_openers_keep_names_but_lowercase_ordinary_words() -> None:
    from box_box.engineer.radio import soften

    assert soften("Overtake") == "overtake"
    assert soften("Lead") == "lead"
    assert soften("Russell", {"Russell"}) == "Russell"
    assert soften("Leclerc's") == "Leclerc's"
    assert soften("P4") == "P4" and soften("DRS") == "DRS" and soften("I'm") == "I'm"


def test_no_mate_after_the_drivers_name() -> None:
    h = Humanizer(random.Random(0))
    assert h._mate("That's fastest lap, Ethan.") is None
    assert h._mate("Good lap.") == "Good lap, mate."


def test_variety_pack_only_uses_fields_each_call_receives() -> None:
    """A template naming a field its caller doesn't pass would crash that call at runtime."""
    import string

    from box_box.engineer.phrases import ORIGINAL_FIELDS
    from box_box.engineer.phrases_variety import VARIETY
    from box_box.engineer.radio import variants

    for key, lines in VARIETY.items():
        assert len(lines) >= 3, key
        for line in lines:
            plain = line.replace("[", " ").replace("]", " ").replace("|", " ")
            fields = {f for _, f, _, _ in string.Formatter().parse(plain) if f}
            assert fields <= ORIGINAL_FIELDS[key], (key, line)
        assert sum(variants(line) for line in lines) >= 6, key


def test_the_same_call_rarely_repeats_word_for_word() -> None:
    from box_box.engineer.radio import Phrasebook

    say = Phrasebook(seed=4)
    said = [say("gained_position", pos=3) for _ in range(12)]
    assert len(set(said)) >= 11  # twelve overtakes, at most one sentence heard twice
