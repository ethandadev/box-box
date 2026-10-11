"""Geometry along the lap: who is ahead of whom on the road, and how far in metres and seconds."""

from __future__ import annotations


def forward_distance(from_dist: float, to_dist: float, track_length: float) -> float:
    """Metres you'd drive forward from `from_dist` to reach `to_dist`, in [0, track_length)."""
    if track_length <= 0:
        return 0.0
    return (to_dist - from_dist) % track_length


def road_offset(me_dist: float, other_dist: float, track_length: float) -> float:
    """Signed on-road distance to another car, in (-L/2, L/2]. Positive = they are ahead of me on the road.

    This is position on the tarmac, not race position: a car a lap down 50 m in front is +50.
    """
    d = forward_distance(me_dist, other_dist, track_length)
    return d - track_length if d > track_length / 2 else d


def time_to_catch(gap_m: float, chaser_kph: float, target_kph: float) -> float | None:
    """Seconds until the chaser closes `gap_m` (>= 0), or None if it isn't closing."""
    closing_ms = (chaser_kph - target_kph) / 3.6
    if closing_ms <= 0.5:  # under ~2 km/h of closing speed is noise, not a catch
        return None
    return gap_m / closing_ms


def gap_seconds(gap_m: float, chaser_kph: float) -> float | None:
    """Time gap as an engineer quotes it: how long until the chaser reaches the point the target is at now."""
    v = chaser_kph / 3.6
    if v < 5:
        return None
    return gap_m / v


def in_zone(lap_fraction: float, zone: tuple[float, float]) -> bool:
    """Zones are (start, end) lap fractions and may wrap the start/finish line (start > end)."""
    start, end = zone
    if start <= end:
        return start <= lap_fraction <= end
    return lap_fraction >= start or lap_fraction <= end
