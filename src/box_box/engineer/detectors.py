"""The full set of detectors the engineer runs every tick."""

from __future__ import annotations

from box_box.engineer.base import Detector
from box_box.engineer.car import CarDetector
from box_box.engineer.energy import EnergyDetector
from box_box.engineer.flags import FlagsDetector
from box_box.engineer.handling import HandlingDetector
from box_box.engineer.laps import LapFeedbackDetector
from box_box.engineer.race import RaceDetector
from box_box.engineer.session import SessionDetector
from box_box.engineer.strategy import StrategyDetector
from box_box.engineer.traffic import TrafficDetector
from box_box.engineer.weather import WeatherDetector


def all_detectors() -> list[Detector]:
    # Order matters only for ties in the same tick: safety-relevant first.
    return [
        FlagsDetector(),
        TrafficDetector(),
        CarDetector(),
        RaceDetector(),
        LapFeedbackDetector(),
        WeatherDetector(),
        EnergyDetector(),
        HandlingDetector(),
        SessionDetector(),
        StrategyDetector(),
    ]
