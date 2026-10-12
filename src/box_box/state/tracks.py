"""What the engineer knows about each circuit: its corners (number, name, where they are) and its character.

Corner numbers and distances come from real F1 data (TracingInsights / FastF1, Apache-2.0); names and
notes are ours. Distances are measured from the start line, like the game's lap distance; we scale them
by the game's track length so small differences between the game and reality don't matter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True, slots=True)
class Corner:
    turn: str  # "9", "2A"
    at_m: float  # distance from the start line on the real circuit
    name: str | None = None

    @property
    def label(self) -> str:
        """How it's said on the radio: 'Copse', 'the hairpin', or 'turn 9'."""
        return self.name or f"turn {self.turn}"

    @property
    def full(self) -> str:
        """'turn 9, Copse' for written context."""
        return f"turn {self.turn}" + (f" ({self.name})" if self.name and self.name != f"turn {self.turn}" else "")


@dataclass(frozen=True, slots=True)
class Track:
    id: int
    name: str
    length_m: int
    corners: tuple[Corner, ...]
    notes: tuple[str, ...]

    def scale(self, game_length: float | None) -> float:
        return game_length / self.length_m if game_length and self.length_m else 1.0

    def corners_on(self, game_length: float | None) -> list[tuple[Corner, float]]:
        """Each corner with its position in the game's lap distance."""
        k = self.scale(game_length)
        return [(c, c.at_m * k) for c in self.corners]

    def corner_near(self, lap_distance: float, game_length: float | None, within: float = 200.0) -> Corner | None:
        """The corner closest to a point on the lap, if one is within `within` metres."""
        best: tuple[float, Corner] | None = None
        for c, at in self.corners_on(game_length):
            d = abs(at - lap_distance)
            if d <= within and (best is None or d < best[0]):
                best = (d, c)
        return best[1] if best else None

    def corner_after(self, lap_distance: float, game_length: float | None, within: float = 600.0) -> Corner | None:
        """The next corner from a point on the lap (wrapping past the line), if within `within` metres."""
        length = game_length or self.length_m
        for c, at in self.corners_on(game_length):
            ahead = (at - lap_distance) % length
            if ahead <= within:
                return c
        return None


@cache
def _all() -> dict[int, Track]:
    raw = json.loads(resources.files("box_box.data").joinpath("tracks.json").read_text(encoding="utf-8"))
    out = {}
    for tid, t in raw["tracks"].items():
        corners = tuple(Corner(c["turn"], float(c["at"]), c.get("name")) for c in t["corners"])
        out[int(tid)] = Track(int(tid), t["name"], int(t["length_m"]), corners, tuple(t["notes"]))
    return out


def get(track_id: int | None) -> Track | None:
    if track_id is None:
        return None
    try:
        return _all().get(track_id)
    except (OSError, ValueError, KeyError):
        return None
