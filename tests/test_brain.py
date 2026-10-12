import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from box_box.brain import intents, tools
from box_box.brain.conversation import Conversation
from box_box.brain.llm import Brain, LLMError, split_sentences
from box_box.config import Settings
from box_box.memory.store import Memory
from box_box.state.model import LapRecord
from tests.factories import Sim


class FakeActions:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def set_verbosity(self, level: str) -> None:
        self.log.append(("verbosity", level))

    def mute(self, minutes: float) -> None:
        self.log.append(("mute", minutes))

    def unmute(self) -> None:
        self.log.append(("unmute",))

    def set_gap_every(self, laps: int) -> None:
        self.log.append(("gap_every", laps))

    def set_callout(self, category: str, enabled: bool) -> None:
        self.log.append(("callout", category, enabled))

    def remember(self, note: str) -> None:
        self.log.append(("remember", note))


def race_sim() -> Sim:
    sim = Sim([])
    sim.lap(0, position=3, lap_num=6, delta_to_leader_ms=4200)
    sim.lap(1, position=1, delta_to_leader_ms=0)
    sim.lap(2, position=2, delta_to_leader_ms=2900)
    sim.lap(3, position=4, delta_to_leader_ms=5000)
    sim.car_damage(0, tyre_wear=(30.0, 31.0, 22.0, 24.0))
    sim.tick()
    return sim


@pytest.mark.parametrize(
    ("said", "expect"),
    [
        ("Radio check", "Loud and clear, Ethan."),
        ("what's the gap?", "Leclerc is 1.3 ahead."),
        ("gap behind", "Hamilton is 0.8 behind."),
        ("How are the tyres", "Mediums, 3 laps old, 27 percent wear"),
        ("where's Norris", "Norris is P1, 4.2 ahead of you."),
        ("what position am I", "You're P3."),
        ("how many laps left", "15 laps to go"),
        ("any damage?", "Car looks fine, no damage."),
        ("weather?", "It's clear, track 30 degrees. No rain"),
        ("box box", "Copy, box this lap."),
        ("fuel", "Fuel's fine, about 0.5 laps in hand."),
    ],
)
def test_intents_answer_from_live_data(said: str, expect: str) -> None:
    sim = race_sim()
    ans = intents.answer(said, sim.world, FakeActions(), "Ethan")
    assert ans is not None and ans.text.startswith(expect), ans and ans.text


@pytest.mark.parametrize(
    ("said", "action"),
    [
        ("less radio please", ("verbosity", "quiet")),
        ("keep me updated", ("verbosity", "chatty")),
        ("radio silence for 3 minutes", ("mute", 3)),
        ("you can talk again", ("unmute",)),
        ("gaps every lap", ("gap_every", 1)),
        ("no more tyre info", ("callout", "tyres", False)),
        ("remember that I struggle in turn 9", ("remember", "i struggle in turn 9")),
    ],
)
def test_commands_change_settings(said: str, action: tuple) -> None:
    acts = FakeActions()
    ans = intents.answer(said, race_sim().world, acts, "Ethan")
    assert ans is not None and ans.command and acts.log == [action]


def test_unknown_is_left_for_claude() -> None:
    assert intents.answer("should we try a two stop here?", race_sim().world, FakeActions(), "Ethan") is None


def test_snapshot_is_compact_and_factual() -> None:
    snap = tools.snapshot(race_sim().world)
    assert "P3" in snap and "Leclerc ahead +1.3s" in snap and "Hamilton behind -0.8s" in snap
    assert len(snap) < 300


def test_snapshot_says_when_the_race_is_over() -> None:
    sim = race_sim()
    sim.lap(0, result_status=3)
    sim.tick()
    assert tools.snapshot(sim.world).startswith("RACE FINISHED")


def test_split_sentences() -> None:
    done, rest = split_sentences("Copy. Gap is 1.2 to Leclerc. He's on")
    assert done == ["Copy.", "Gap is 1.2 to Leclerc."] and rest == "He's on"


# ---- Claude, with a fake API ------------------------------------------------------------------------


class FakeStream:
    def __init__(self, turn: dict) -> None:
        self.turn = turn

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *a) -> None:  # type: ignore[no-untyped-def]
        pass

    def __aiter__(self):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            await asyncio.sleep(self.turn.get("delay", 0))
            for chunk in self.turn.get("text", []):
                yield SimpleNamespace(type="text", text=chunk)

        return gen()

    async def get_final_message(self):  # type: ignore[no-untyped-def]
        content = [SimpleNamespace(type="text", text="".join(self.turn.get("text", [])))]
        for i, (name, args) in enumerate(self.turn.get("tools", [])):
            content.append(SimpleNamespace(type="tool_use", id=f"t{i}", name=name, input=args))
        return SimpleNamespace(stop_reason="tool_use" if self.turn.get("tools") else "end_turn", content=content)


class FakeClient:
    def __init__(self, turns: list[dict]) -> None:
        self.turns = turns
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kw):  # type: ignore[no-untyped-def]
        self.requests.append(kw)
        return FakeStream(self.turns[len(self.requests) - 1])


def make_brain(turns: list[dict]) -> tuple[Brain, FakeClient, Settings]:
    settings = Settings()
    settings.ai.api_key = "sk-test"
    brain = Brain(lambda: settings, lambda: "- prefers gaps every lap", lambda: ["Box this lap."])
    client = FakeClient(turns)
    brain.backend().client = client
    return brain, client, settings


def test_claude_tool_loop_streams_sentences() -> None:
    brain, client, _ = make_brain(
        [
            {"tools": [("driver", {"name": "Leclerc"})]},
            {"text": ["Leclerc is 1.", "3 ahead on mediums. ", "Same age as yours."]},
        ]
    )
    sim = race_sim()

    async def run() -> list[str]:
        return [s async for s in brain.reply("how's Leclerc doing", sim.world, FakeActions())]

    out = asyncio.run(run())
    assert out == ["Leclerc is 1.3 ahead on mediums.", "Same age as yours."]
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and json.loads(tool_result["content"])["name"] == "Leclerc"
    system = client.requests[0]["system"][0]
    assert "Jake" in system["text"] and "Ethan" in system["text"] and "gaps every lap" in system["text"]
    assert len(brain.history) == 2
    context = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "P3" in context and "Box this lap." in context


def test_conversation_routes_and_falls_back() -> None:
    sim = race_sim()
    acts = FakeActions()

    async def collect(conv: Conversation, said: str) -> list[str]:
        return [s async for s in conv.handle(said, sim.world)]

    # Fast answers on: a gap question never waits for Claude.
    brain, client, settings = make_brain([{"text": ["Should not be used."]}])
    conv = Conversation(brain, acts, lambda: settings)
    assert asyncio.run(collect(conv, "what's the gap")) == ["Leclerc is 1.3 ahead."] and not client.requests

    # Open question: the AI answers after a beat, no filler...
    brain, client, settings = make_brain(
        [{"delay": 1.8, "text": ["Two stop could work. ", "Tyres are holding though."]}]
    )
    conv = Conversation(brain, acts, lambda: settings)
    out = asyncio.run(collect(conv, "should we try a two stop"))
    assert out == ["Two stop could work.", "Tyres are holding though."]

    # ...and only a really slow answer gets "Stand by" first.
    from box_box.brain import conversation as conv_mod

    conv_mod.STAND_BY_AFTER_S, saved = 0.3, conv_mod.STAND_BY_AFTER_S
    try:
        brain, client, settings = make_brain([{"delay": 0.8, "text": ["Two stop could work."]}])
        out = asyncio.run(collect(Conversation(brain, acts, lambda: settings), "should we try a two stop"))
        assert out[0].startswith("Stand by") and out[1:] == ["Two stop could work."]
    finally:
        conv_mod.STAND_BY_AFTER_S = saved

    # Claude fails: fall back to the local answer.
    brain, client, settings = make_brain([])
    settings.ai.fast_answers = False

    async def broken(*a, **k):  # type: ignore[no-untyped-def]
        raise LLMError("offline")
        yield  # pragma: no cover

    brain.reply = broken  # type: ignore[method-assign]
    conv = Conversation(brain, acts, lambda: settings)
    assert asyncio.run(collect(conv, "what's the gap")) == ["Leclerc is 1.3 ahead."]

    # No API key at all: still useful.
    settings2 = Settings()
    conv = Conversation(Brain(lambda: settings2, lambda: ""), acts, lambda: settings2)
    assert asyncio.run(collect(conv, "radio check")) == ["Loud and clear, Ethan."]
    assert "Ask me about" in asyncio.run(collect(conv, "tell me a joke"))[0]


# ---- memory -----------------------------------------------------------------------------------------


def test_memory_roundtrip_and_profile(tmp_path: Path) -> None:
    mem = Memory(tmp_path / "m.sqlite3")
    sid = mem.start_session(0xABC, "Monza", "Race")
    assert sid and mem.start_session(0xABC, "Monza", "Race") == sid  # same session uid: same row
    mem.record_lap(sid, LapRecord(2, 81_500, 27_000, 27_500, 27_000, True, 3, 17, 4, 12.0, 50.0, 1.6, False))
    mem.end_session(sid, 81_500, 3)
    mem.add_note("driver", "Hates being told about tyre temps")
    assert mem.track_best("Monza") == 81_500
    profile = mem.driver_profile()
    assert "Hates being told" in profile and "Monza Race, finished P3, best 81.500s" in profile


def test_corrupt_memory_is_set_aside(tmp_path: Path) -> None:
    db = tmp_path / "m.sqlite3"
    db.write_bytes(b"this is not a database" * 100)
    mem = Memory(db)
    mem.add_note("x", "still works")
    assert mem.notes()[0]["text"] == "still works"
    assert list(tmp_path.glob("m.broken-*.sqlite3"))
