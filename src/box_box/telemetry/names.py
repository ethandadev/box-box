"""Readable names for ids in the UDP spec, phrased the way an engineer would say them."""

from __future__ import annotations

TRACKS: dict[int, str] = {
    0: "Melbourne",
    2: "Shanghai",
    3: "Bahrain",
    4: "Barcelona",
    5: "Monaco",
    6: "Montreal",
    7: "Silverstone",
    9: "Hungaroring",
    10: "Spa",
    11: "Monza",
    12: "Singapore",
    13: "Suzuka",
    14: "Abu Dhabi",
    15: "Austin",
    16: "Interlagos",
    17: "Red Bull Ring",
    19: "Mexico City",
    20: "Baku",
    26: "Zandvoort",
    27: "Imola",
    29: "Jeddah",
    30: "Miami",
    31: "Las Vegas",
    32: "Qatar",
    39: "Silverstone Reverse",
    40: "Red Bull Ring Reverse",
    41: "Zandvoort Reverse",
    42: "Madrid",
}

SESSION_TYPES: dict[int, str] = {
    0: "Unknown",
    1: "FP1",
    2: "FP2",
    3: "FP3",
    4: "Short Practice",
    5: "Q1",
    6: "Q2",
    7: "Q3",
    8: "Short Qualifying",
    9: "One-Shot Qualifying",
    10: "Sprint Shootout 1",
    11: "Sprint Shootout 2",
    12: "Sprint Shootout 3",
    13: "Short Sprint Shootout",
    14: "One-Shot Sprint Shootout",
    15: "Race",
    16: "Race 2",
    17: "Race 3",
    18: "Time Trial",
}

PRACTICE_SESSIONS = frozenset({1, 2, 3, 4})
QUALI_SESSIONS = frozenset({5, 6, 7, 8, 9, 10, 11, 12, 13, 14})
RACE_SESSIONS = frozenset({15, 16, 17})
TIME_TRIAL = 18

TEAMS: dict[int, str] = {
    0: "Mercedes",
    1: "Ferrari",
    2: "Red Bull",
    3: "Williams",
    4: "Aston Martin",
    5: "Alpine",
    6: "Racing Bulls",
    7: "Haas",
    8: "McLaren",
    9: "Sauber",
    104: "My Team",
    476: "Mercedes",
    477: "Ferrari",
    478: "Red Bull",
    479: "Williams",
    480: "Aston Martin",
    481: "Alpine",
    482: "Racing Bulls",
    483: "Haas",
    484: "McLaren",
    485: "Audi",
    486: "Cadillac",
}

# Visual compound is what the driver sees on the sidewall.
VISUAL_COMPOUNDS: dict[int, str] = {
    7: "inters",
    8: "wets",
    16: "softs",
    17: "mediums",
    18: "hards",
    19: "super softs",
    20: "softs",
    21: "mediums",
    22: "hards",
}

WEATHER: dict[int, str] = {
    0: "clear",
    1: "light cloud",
    2: "overcast",
    3: "light rain",
    4: "heavy rain",
    5: "storm",
}


def track_name(track_id: int) -> str:
    return TRACKS.get(track_id, f"track {track_id}")


def session_name(session_type: int) -> str:
    return SESSION_TYPES.get(session_type, f"session {session_type}")


def team_name(team_id: int) -> str:
    return TEAMS.get(team_id, f"team {team_id}")


def compound_name(visual_compound: int) -> str:
    return VISUAL_COMPOUNDS.get(visual_compound, "unknown tyres")


def surname(full_name: str) -> str:
    """How a driver is said on the radio.

    F1 25 sends AI drivers as an uppercase surname ("NORRIS", "PÉREZ"), sometimes with a first name
    ("Max VERSTAPPEN"). Online players' names pass through unchanged.
    """
    name = full_name.strip()
    if not name:
        return name
    words = name.split()
    upper = [w for w in words if w.isupper() and any(ch.isalpha() for ch in w)]
    if upper:
        # Surname is the run of uppercase words ("DE VRIES"); title-case it, keeping particles natural.
        return " ".join(_title(w) for w in upper)
    return name


def _title(word: str) -> str:
    return "-".join(part[:1].upper() + part[1:].lower() for part in word.split("-"))
