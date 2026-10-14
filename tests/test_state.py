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


def test_sprint_recording_is_labelled_once_the_weekend_arrives(tmp_path) -> None:
    from box_box.telemetry import names
    from box_box.telemetry.autorecord import AutoRecorder

    rec = AutoRecorder(tmp_path)
    rec.feed(b"x" * 40, 1, 10, 15, weekend=())  # the first Session packet: weekend not filled in yet
    for _ in range(300):
        rec.feed(b"x" * 40, 1, 10, 15, weekend=(10, 11, 12, 15, 16))
    saved = rec.close()
    assert saved is not None and f"{names.track_name(10)} Sprint" in saved.name


def test_listing_recordings_skips_files_that_vanish(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from box_box.telemetry.autorecord import list_recordings

    (tmp_path / "a.f1raw.gz").write_bytes(b"x")
    gone = tmp_path / "b.f1raw.gz"
    gone.write_bytes(b"x")
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self.name == gone.name:
            raise FileNotFoundError(self)  # pruned between the glob and the stat
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert [r["name"] for r in list_recordings(tmp_path)] == ["a.f1raw.gz"]


def test_f2_wets_have_a_name() -> None:
    from box_box.telemetry.names import compound_name

    assert compound_name(15) == "wets"
