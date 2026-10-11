"""Where Box Box keeps its files. Override everything with the BOXBOX_HOME environment variable."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_home() -> Path:
    override = os.environ.get("BOXBOX_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "BoxBox"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "BoxBox"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "box-box"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub(name: str) -> Path:
    path = app_home() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return app_home() / "settings.json"


def logs_dir() -> Path:
    return sub("logs")


def models_dir() -> Path:
    return sub("models")


def default_recordings_dir() -> Path:
    return sub("recordings")


def database_file() -> Path:
    return app_home() / "memory.sqlite3"
