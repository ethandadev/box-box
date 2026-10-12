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
