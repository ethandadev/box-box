"""Handles what the driver says: instant local answers for the common things, the AI for the rest."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Callable

from box_box.brain import intents
from box_box.brain.llm import Brain, LLMError
from box_box.bus import Bus
from box_box.config import Settings
from box_box.engineer.radio import Phrasebook, lap_time, tenths
from box_box.state.model import World
from box_box.telemetry import names

log = logging.getLogger(__name__)

STAND_BY_AFTER_S = 1.5
FEEDBACK_WINDOW_S = 75.0  # how long after "How's the balance?" an answer counts as feedback
QUESTION_START = re.compile(
    r"^(?:what|what's|whats|how|how's|hows|where|where's|who|who's|when|why|is|are|can|could|should|do|does|did|"
    r"any|have|has|will|would|tell me|give me)\b",
    re.I,
)


def is_question(text: str) -> bool:
    t = text.strip()
    return t.endswith("?") or bool(QUESTION_START.match(t))


class Conversation:
    def __init__(
        self, brain: Brain, actions: intents.Actions, settings: Callable[[], Settings], bus: Bus | None = None
    ) -> None:
        self.brain = brain
        self.actions = actions
        self._settings = settings
        self.bus = bus or Bus()
        self.say = Phrasebook()
        self.awaiting: tuple[str, float] | None = None  # (topic, when asked)

    def expect_feedback(self, topic: str) -> None:
        """The engineer just asked the driver something ("How's the balance?"): the next answer is feedback."""
        self.awaiting = (topic, time.monotonic())

    def _feedback_topic(self, text: str, local: intents.Answer | None) -> str | None:
        if self.awaiting is None:
            return None
        topic, asked_at = self.awaiting
        if time.monotonic() - asked_at > FEEDBACK_WINDOW_S:
            self.awaiting = None
            return None
        if (local and local.command) or is_question(text):
            return None
        self.awaiting = None
        return topic

    async def handle(self, transcript: str, world: World) -> AsyncIterator[str]:
        s = self._settings()
        self.say.personality = s.engineer.personality
        text = transcript.strip()
        if not text:
            yield self.say("didnt_catch", me=s.driver.name)
            return
        self.bus.publish("driver", text=text)
        local = intents.answer(text, world, self.actions, s.driver.name)
        llm_text = text
        topic = self._feedback_topic(text, local)
        if topic:
            where = (
                f"{names.track_name(world.session.track_id)} {names.session_name(world.session_type)}"
                if world.session
                else "a session"
            )
            self.actions.remember(f"{topic.capitalize()} feedback at {where}: {text}")
            if not self.brain.available:
                yield self.say("feedback_ack", me=s.driver.name)
                return
            local = None  # it's feedback, not a question about tyres or whatever it mentioned
            llm_text = f"(answering your question about the {topic}) {text}"
        use_llm = self.brain.available and not (local and (local.command or s.ai.fast_answers))
        if not use_llm:
            if local:
                yield local.text
            elif self.brain.disabled_reason:
                yield "I can only do the basics right now. Check the AI settings after the session."
                self.bus.publish("warning", message=self.brain.disabled_reason)
            else:
                yield "Sorry, I can't help with that one. Ask me about gaps, tyres, fuel, damage or the weather."
            return

        queue: asyncio.Queue[str | None | Exception] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for sentence in self.brain.reply(llm_text, world, self.actions):
                    await queue.put(sentence)
            except Exception as e:  # noqa: BLE001
                await queue.put(e)
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        said_anything = False
        try:
            first = True
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), STAND_BY_AFTER_S) if first else await queue.get()
                except TimeoutError:
                    yield self.say("stand_by")
                    first = False
                    continue
                first = False
                if item is None:
                    break
                if isinstance(item, Exception):
                    log.warning("AI reply failed: %s", item)
                    if isinstance(item, LLMError):
                        self.bus.publish("warning", message=str(item))
                    if not said_anything:
                        yield local.text if local else "Radio's breaking up, I'll come back to you on that."
                    break
                said_anything = True
                yield item
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def debrief(self, world: World) -> str | None:
        """A short spoken debrief at the end of a session, and a note for next time."""
        s = self._settings()
        me = world.player
        if me is None or me.lap is None or world.session is None:
            return None
        valid = me.valid_laps()
        best = me.session_best_ms
        facts = [
            f"Session: {names.track_name(world.session.track_id)} {names.session_name(world.session_type)}.",
            f"Finished P{me.lap.position}" + (f" from P{me.lap.grid_position} on the grid." if world.is_race else "."),
            f"Laps completed: {len(me.laps)}, clean push laps: {len(valid)}.",
        ]
        if best:
            facts.append(f"Best lap {lap_time(best)}.")
            overall = world.best_lap_overall()
            if overall and overall[0] is not me:
                facts.append(
                    f"Fastest overall {lap_time(overall[1])} by {overall[0].name}, {tenths((best - overall[1]) / 1000)} quicker."
                )
        if len(valid) >= 3:
            times = [r.lap_ms for r in valid]
            spread = (sorted(times)[len(times) // 2] - min(times)) / 1000
            facts.append(f"Median lap was {tenths(spread)} off the best.")
            losses = [0.0, 0.0, 0.0]
            bests = [
                min(r.sector1_ms for r in valid),
                min(r.sector2_ms for r in valid),
                min(r.sector3_ms for r in valid),
            ]
            for r in valid:
                for i, v in enumerate((r.sector1_ms, r.sector2_ms, r.sector3_ms)):
                    losses[i] += (v - bests[i]) / 1000
            facts.append(f"Most inconsistent sector: {losses.index(max(losses)) + 1}.")
        if me.damage and max(me.damage.front_left_wing, me.damage.front_right_wing, me.damage.floor) >= 20:
            facts.append("Car came back with damage.")
        summary = " ".join(facts)
        self.actions.remember(
            f"{names.track_name(world.session.track_id)} {names.session_name(world.session_type)}: {summary}"
        )
        if not (self.brain.available and s.ai.debrief):
            return None
        prompt = (
            f"The session just ended. Give {s.driver.name} the post-session radio, like a real F1 engineer on the "
            "in-lap: open with the result, one specific thing that went well, one thing we'll review, and a short "
            "warm close. Three or four short sentences, spoken, no lists. Examples of the tone: "
            "'P3, well done mate. Pace was good in clean air. We'll look at the stop. Good points today.' / "
            "'Not our day. We'll review. You drove well with what we had.' "
            f"Facts: {summary}"
        )
        try:
            return await self.brain.complete(prompt)
        except LLMError as e:
            log.warning("debrief failed: %s", e)
            return None
