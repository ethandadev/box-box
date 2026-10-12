"""Shared test setup."""

import pytest

from box_box.engineer import human


@pytest.fixture(autouse=True)
def scripted_speech(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Natural-speech slips are random; keep every test except the humanizer's own deterministic."""
    if request.module.__name__.endswith("test_human"):
        return
    monkeypatch.setattr(human.Humanizer, "apply", lambda self, text, *a, **k: text)
