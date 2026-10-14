from pathlib import Path

import pytest

from box_box.state.model import World
from box_box.telemetry.packets import LAYOUTS, Decoder, DriverStatus, PacketId
from box_box.telemetry.rawfile import RawWriter, read_records
from tests.builders import header, participants_packet

FIXTURES = Path(__file__).parent / "fixtures"


def replay(name: str) -> tuple[Decoder, World, dict]:
    decoder, world, last = Decoder(), World(), {}
    for _, data in read_records(FIXTURES / name):
        packet = decoder.decode(data)
        if packet:
            last[packet.id] = packet
            world.apply(packet)
    return decoder, world, last


@pytest.mark.parametrize("fixture", sorted(p.name for p in FIXTURES.glob("*.f1raw")))
def test_fixtures_decode_cleanly(fixture: str) -> None:
    decoder, world, last = replay(fixture)
    assert decoder.last_warning is None
    assert world.session is not None and world.track_length > 3000
    me = world.player
    assert me is not None and me.lap is not None and me.telemetry is not None
    assert 0 <= me.lap.lap_distance <= world.track_length or me.lap.lap_distance < 0
    assert 0 <= me.speed_kph < 400
    # Race positions of running cars are a permutation of 1..N.
    positions = sorted(c.lap.position for c in world.cars if c.lap and c.lap.result_status >= 2)
    assert positions == list(range(1, len(positions) + 1))


def test_format_differences() -> None:
    _, w25, _ = replay("race-2025-melbourne-lap.f1raw")
    _, w26, last26 = replay("race-2026-monza-finish.f1raw")
    assert len(w25.cars) == 22 and len(w26.cars) == 24
    assert w25.player.status.ers_harvest_limit_j is None
    assert w26.player.status.ers_harvest_limit_j == pytest.approx(6.0e6)
    assert w26.player.telemetry2 is not None and w26.player.telemetry2.regulations_2026
    # Monza's active aero zone wraps the line.
    start, end = w26.session.active_aero_zones[0]
    assert start > end
    assert w26.is_race and w26.session.track_length == 5798


def test_quantised_g_forces_are_scaled() -> None:
    _, world, _ = replay("race-2026-spa-wet-flashback.f1raw")
    m = world.player.motion
    assert m is not None
    assert all(abs(g) < 8 for g in (m.g_lat, m.g_lon, m.g_vert))


def test_flashback_bumps_timeline() -> None:
    _, world, _ = replay("race-2026-spa-wet-flashback.f1raw")
    assert world.timeline == 3  # new session + two flashbacks


def test_one_flashback_rewinds_once() -> None:
    """The FLBK event arrives with the old session time, then the clock jumps back: that's one rewind."""
    decoder, world = Decoder(), World()
    bumps: list[int] = []  # timeline bumps between one flashback event and the next
    for _, data in read_records(FIXTURES / "race-2026-spa-wet-flashback.f1raw"):
        packet = decoder.decode(data)
        if packet is None:
            continue
        before = world.timeline
        world.apply(packet)
        if packet.id == PacketId.EVENT and packet.data.code == "FLBK":
            bumps.append(0)
        if bumps:
            bumps[-1] += world.timeline - before
    assert bumps == [1, 1]


def test_time_trial_player_on_push_lap() -> None:
    _, world, _ = replay("tt-2026-monza-flashback.f1raw")
    assert world.session.session_type == 18
    assert world.player.driver_status == DriverStatus.FLYING_LAP


@pytest.mark.parametrize("fmt", [2025, 2026])
def test_participants_roundtrip(fmt: int) -> None:
    data = participants_packet(["Max VERSTAPPEN", "Ethan"], fmt=fmt)
    packet = Decoder().decode(data)
    assert packet is not None and packet.id == PacketId.PARTICIPANTS
    assert packet.num_active_cars == 2
    assert len(packet.data) == LAYOUTS[fmt].max_cars
    assert packet.data[0].name == "Max VERSTAPPEN"
    assert packet.data[1].race_number == 2


def test_wrong_size_and_format_are_dropped_with_a_warning() -> None:
    d = Decoder()
    assert d.decode(header(PacketId.LAP_DATA) + b"\x00" * 10) is None
    assert "expected 1399" in (d.last_warning or "")
    bad = bytearray(header(PacketId.LAP_DATA))
    bad[0:2] = (2024).to_bytes(2, "little")
    assert d.decode(bytes(bad)) is None
    assert "UDP Format" in (d.last_warning or "")


def test_rawfile_roundtrip_ignores_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "x.f1raw"
    with RawWriter(path) as w:
        w.write(1, b"abc")
        w.write(2, b"defg")
    with open(path, "ab") as f:
        f.write(b"\x03\x00\x00\x00\x00\x00\x00\x00\x09\x00par")  # record cut off mid-body
    assert list(read_records(path)) == [(1, b"abc"), (2, b"defg")]


def test_gzip_recording_roundtrip_and_killed_mid_write(tmp_path: Path) -> None:
    src = FIXTURES / "race-2026-monza-finish.f1raw"
    path = tmp_path / "x.f1raw.gz"
    with RawWriter(path) as w:
        for t, data in read_records(src):
            w.write(t, data)
    assert path.stat().st_size < src.stat().st_size / 4
    assert list(read_records(path)) == list(read_records(src))
    # A recording killed mid-write has no gzip trailer: keep everything readable before the cut.
    cut = tmp_path / "cut.f1raw.gz"
    cut.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    partial = list(read_records(cut))
    assert 0 < len(partial) < len(list(read_records(src)))
