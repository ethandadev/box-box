"""World-model details found in review: corner lookup, names, lap-time corrections."""

from box_box.state import tracks
from box_box.telemetry.names import surname


def test_next_corner_is_the_nearest_not_the_first_listed() -> None:
    imola = next(t for t in tracks._all().values() if t.name.lower().startswith("imola"))
    last = imola.corners[-1]
    at = last.at_m * imola.scale(imola.length_m) - 40  # just before the final corner
    assert imola.corner_after(at, imola.length_m, within=500) == last


def test_surnames_only_from_the_games_format() -> None:
    assert surname("Max VERSTAPPEN") == "Verstappen"
    assert surname("Nyck DE VRIES") == "De Vries"
    assert surname("TJ Smith") == "TJ Smith"  # an online player's own name
    assert surname("Lewis H") == "Lewis H"
