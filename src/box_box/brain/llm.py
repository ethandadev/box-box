"""The engineer's brain for open conversation: any AI service, streaming, tools, radio-style replies.

The service-specific wire protocols live in `backends.py`; this module owns what is the same for all of
them: who the engineer is (the system prompt), what he can look up (the tools), and the conversation.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from box_box.brain import providers, tools
from box_box.brain.backends import LLMError, SentenceStream, clean_for_radio, make_backend, split_sentences
from box_box.brain.intents import Actions
from box_box.config import Settings
from box_box.state.model import World

__all__ = ["Brain", "LLMError", "clean_for_radio", "split_sentences"]

log = logging.getLogger(__name__)

PERSONALITY = {
    "calm": "Calm, precise and quietly supportive, like the best F1 race engineers. You raise your voice only "
    "for big moments, and a 'well done, mate' from you means something.",
    "fired_up": "Energetic and passionate. You get properly loud for overtakes, purple laps and results, "
    "but you stay clear and short when it's busy.",
    "dry": "Calm with a dry, deadpan sense of humour. Occasionally a wry one-liner when things are quiet, "
    "never when it's busy and never at the expense of clarity.",
}

NATURAL = {
    "off": "Speak cleanly, like a scripted message.",
    "subtle": "Sound like a person keying a radio, not a script: you may open with 'Yeah,' or 'Okay, so,' and "
    "very occasionally say 'uh' mid-sentence. Never in urgent calls.",
    "human": "Sound like a person keying a radio: open with 'Yeah,' / 'Okay, so,' when natural, say 'uh' now and "
    "then, call the driver 'mate' sometimes, and say 'copy, copy' when acknowledging. Never in urgent calls.",
}

SYSTEM = """You are {engineer}, race engineer for {driver} in their EA Sports F1 25 career, racing against AI drivers. \
You talk to {driver} over team radio while they drive at 300 km/h. Everything you write is read aloud by \
text-to-speech over a crackly radio.

You are a real race engineer, not an assistant. You own the strategy, you have opinions, and you have \
{driver}'s back. You think one step ahead: what the gap means, what the rivals' tyres mean, when the \
stop should be, what could go wrong. When {driver} asks something, answer it and, if it matters, add the \
one thing they should do about it.

How real F1 radio sounds (match this):
- "Sainz 1.2 ahead, last lap 47.7, six lap offset, he's on the medium."
- "Okay, Carlos has stayed out. We've already got the gap, so the opportunity is to pass the cars we've \
got a tyre advantage over."
- "These tyres are robust, happy to push on them."
- "Still plan A for now." / "Thinking plan B. Plan B." / "We're in the window for a safety car."
- "Hamilton yet to stop, he started on the hard."
- "Copy. Box this lap, box this lap. Confirm on the MFD."
- "Good lap. Clean air is king, keep it going."
- "Ten laps to go. Ten of your finest."
- "Currently thinking plan A, target lap 22." / "Target is to get to the end on this tyre."
- "We've got a big offset on tyres. You might have some fun towards the end."
- "You're clearly the fastest car and have nothing to prove. Bring it home."
- "Okay mate, let's get back in the rhythm." / "Looks like overheating. Get back in the rhythm, it should help."
- Practice: "Yeah, we'll check, we'll compare to the other car." / "Everything's fine, good chance to get a feel for the car." / "Mid to exit turn 1, you can take less kerb."
- "Copy, copy." / "Yeah, saw that one. All okay?" / "Understood, we're looking into it."
- "Possible rain in 20 laps. It'll be heavy if it hits." / "Push and hold, and we'll box this lap."
- "P2, just 11 thousandths to pole. So tight, really good effort."
- "P3, not a bad day. You earned your money today." / "Damage limitation, but we'll take the points."
- When the driver is frustrated: steady them in a few words, then give them something useful to do.
  Driver: "The rears are just done." You: "Yep, I see it. Even with that, you're still catching the cars ahead."
  Driver: "Why? I was managing so much!" You: "I'm asking the same question. We'll find it. For now, lift and coast into turn one."
- If you don't know yet, say so and come back: "Stand by, we're looking." Never guess.
- Just answer. Never open with "Let me check", "Sure", "Great question", or by repeating the question.
- Under a safety car or red flag: calm, procedural, short. "Red flag, red flag. No overtaking. Pit limiter on the way in."

Rules for every reply:
- One to three short sentences, usually under 25 words total. Lead with the answer. No filler, no \
"great question", no sign-offs, no questions back unless you genuinely need their call (plan A or B?).
- Plain spoken English only. No lists, markdown, emoji, brackets or stage directions.
- Numbers the way engineers say them: gaps "1.2", deltas "three tenths", positions "P4", lap times \
"1:26.4", percentages "40 percent". Round sensibly; never read out more than one decimal.
- Use {driver}'s name sparingly, for emphasis or warmth.
- {natural}
- Repeat a critical instruction once for clarity ("Box now. Box now.").
- {personality}

Facts:
- Never invent data. Use the live pit-wall snapshot in the message or call a tool. If the data isn't \
there, say you don't have it yet.
- If the snapshot says RACE FINISHED, the race is over: celebrate, commiserate or debrief. Never talk about laps to go or bringing it home after the flag.
- Recent radio is listed so you don't repeat it: add something new, don't restate a call {driver} just heard.
- You can't touch the car or the game. If {driver} wants to pit or change something, confirm and tell them \
to do it on the MFD.
- Name corners like an engineer: "turn 9" or its name ("Copse", "the hairpin"). Use the track tool for
corner names, overtaking spots and where {driver} is losing time; never guess a corner name.
- If {driver} gives feedback (balance, tyres, the car), acknowledge it like an engineer ("Copy, understeer \
in the slow stuff, noted") and save anything lasting with the remember tool.
- If {driver} asks you to talk more or less, change how often you give gaps, go quiet for a while, or \
remember something, use the radio_preferences or remember tools and confirm in a few words.
- If you can't understand what {driver} said, ask them to say again.
- Stay in character as the engineer on the pit wall. Never mention being an AI, tools, data feeds or \
telemetry packets.

What you know about {driver} from previous sessions:
{profile}"""

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "session_info",
        "description": "Track, session type, weather, safety car, laps or time remaining, the driver's position and the pit window.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "gaps",
        "description": "Race: gaps to the cars directly ahead and behind and to the leader. Practice/quali: the driver's best lap vs the fastest.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "standings",
        "description": "Current order with each car's tyres, tyre age, pit stops and best/last lap.",
        "parameters": {
            "type": "object",
            "properties": {"top": {"type": "integer", "description": "How many positions, default 10."}},
        },
    },
    {
        "name": "driver",
        "description": "Everything about one rival: position, gap to us, tyres, pit stops, best lap.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Driver surname, e.g. 'Leclerc'."}},
            "required": ["name"],
        },
    },
    {
        "name": "tyres",
        "description": "Our tyres: compound, age, wear per corner, wear rate, laps of life left, temperatures, pressures.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "car_health",
        "description": "Our damage (wings, floor, diffuser, sidepods, engine, gearbox), faults, brake and engine temps.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "fuel",
        "description": "Fuel on board and, in a race, the margin at the finish.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "energy",
        "description": "Battery level, deploy mode, overtake mode availability, active aero mode (2026 cars).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "weather",
        "description": "Current weather and the forecast.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "lap_times",
        "description": "Recent lap and sector times for us or a named driver.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Driver surname; omit for our own laps."},
                "last": {"type": "integer", "description": "How many recent laps, default 5."},
            },
        },
    },
    {
        "name": "strategy",
        "description": "Pit window, predicted rejoin position, tyre life estimate, laps remaining, and the tyres of the cars around us.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "track",
        "description": "The circuit: corner numbers and names, where overtaking happens, its character, the next "
        "corner, and where the driver is losing time versus their best lap (corner, apex speed).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "radio_preferences",
        "description": "Change how the engineer talks: verbosity, how often to give gaps, or radio silence for some minutes.",
        "parameters": {
            "type": "object",
            "properties": {
                "verbosity": {"type": "string", "enum": ["quiet", "normal", "chatty"]},
                "gap_every_laps": {"type": "integer", "minimum": 1, "maximum": 10},
                "silence_minutes": {"type": "number", "minimum": 0, "maximum": 60},
            },
        },
    },
    {
        "name": "remember",
        "description": "Remember a lasting note about the driver (a preference, a weakness, something to bring up later).",
        "parameters": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]},
    },
]


LOCAL_TOOLS = {"radio_preferences", "remember"}


MAX_REPLY_SENTENCES = 3  # an engineer on the radio, not an essay


def session_mode(world: World) -> str | None:
    """What kind of session this is, so the AI talks practice in practice and race in a race."""
    if world.session is None:
        return None
    if world.is_practice:
        return (
            "This is PRACTICE: no race is happening. Talk run plans, the car, balance, tyres, setup and lap "
            "times; the timesheet barely matters. Never talk track position, defending, overtaking or race strategy."
        )
    if world.is_qualifying:
        return (
            "This is QUALIFYING: one fast lap at a time. Out lap, push lap, the cutoff and track position on the road."
        )
    if world.is_time_trial:
        return "This is TIME TRIAL: just the driver and the clock. No other cars, no strategy; lap times and corners."
    if world.is_sprint:
        return "This is the SPRINT: a short race with no pit stop planned. Flat out, positions and gaps, no strategy."
    if world.is_race:
        return "This is the RACE."
    return None


class Brain:
    """Conversation with whichever AI service is configured. Safe to use when it isn't: check `available`."""

    def __init__(
        self,
        settings: Callable[[], Settings],
        profile: Callable[[], str],
        recent_radio: Callable[[], list[str]] | None = None,
    ) -> None:
        self._settings = settings
        self._profile = profile
        self._recent_radio = recent_radio or (lambda: [])
        self._backend: Any = None
        self._backend_key: tuple[str, str, str] | None = None
        self.history: list[dict[str, Any]] = []
        self._lone_think: set[str] = set()  # models seen opening with bare reasoning and a lone </think>
        self.status = "not configured"
        self.last_error: str | None = None
        self.disabled_reason: str | None = None
        self.last_latency: float | None = None  # seconds to the first spoken sentence

    # ---- configuration -------------------------------------------------------------------------------

    @property
    def provider(self) -> providers.Provider:
        return self._settings().provider

    @property
    def model(self) -> str:
        s = self._settings()
        return providers.model_for(s.provider, s.ai)

    @property
    def problem(self) -> str | None:
        """Why the AI can't be used right now, or None."""
        s = self._settings()
        if not s.ai.enabled:
            return "AI conversation is off."
        return s.ai_problem or self.disabled_reason

    @property
    def available(self) -> bool:
        return self.problem is None

    def describe(self) -> str:
        """'Anthropic Claude, claude-haiku-5-5' for status lines."""
        model = self.model or "the loaded model"
        return f"{self.provider.label}, {model}"

    def reset_errors(self) -> None:
        self.disabled_reason = None
        self.last_error = None
        reset = getattr(self._backend, "reset", None)
        if callable(reset):
            reset()

    def backend(self) -> Any:
        s = self._settings()
        p = s.provider
        key = (p.id, providers.key_for(p, s.ai), providers.base_url_for(p, s.ai))
        if self._backend is None or key != self._backend_key:
            old = self._backend
            self._backend = make_backend(p, key[1], key[2])
            self._backend_key = key
            self.disabled_reason = None
            if old is not None:
                self._close_later(old)
        return self._backend

    @staticmethod
    def _close_later(backend: Any) -> None:
        import asyncio

        try:
            asyncio.get_running_loop().create_task(backend.aclose())
        except RuntimeError:
            pass  # no loop: it'll be garbage collected

    async def aclose(self) -> None:
        if self._backend is not None:
            try:
                await self._backend.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._backend = None
            self._backend_key = None

    def system_prompt(self) -> str:
        s = self._settings()
        return SYSTEM.format(
            engineer=s.engineer.name,
            driver=s.driver.name,
            personality=PERSONALITY.get(s.engineer.personality, PERSONALITY["calm"]),
            natural=NATURAL.get(s.engineer.natural_speech, NATURAL["subtle"]),
            profile=self._profile() or "- Nothing yet. This is one of your first sessions together.",
        )

    def _context(self, world: World) -> str:
        parts = [f"[Live pit wall: {tools.snapshot(world)}]"]
        mode = session_mode(world)
        if mode:
            parts.append(f"[{mode}]")
        recent = self._recent_radio()
        if recent:
            parts.append("[Your own recent calls to the driver, oldest first: " + " | ".join(recent[-5:]) + "]")
        return "\n".join(parts)

    def _fail(self, e: LLMError) -> None:
        self.last_error = str(e)
        if e.fatal:
            self.disabled_reason = str(e)
            self.status = "error"
        elif "rate limit" in str(e).lower():
            self.status = "rate limited"
        else:
            self.status = "offline"

    # ---- talking -------------------------------------------------------------------------------------

    async def reply(self, transcript: str, world: World, actions: Actions) -> AsyncIterator[str]:
        """Stream the engineer's answer, sentence by sentence. Raises LLMError."""
        s = self._settings()
        # A model that's been seen opening with bare reasoning and a lone </think> is held until it closes.
        out = SentenceStream(hold=self.model in self._lone_think)
        started = time.perf_counter()
        said = f"{s.driver.name}: {transcript}"

        def run_tool(name: str, args: dict[str, Any]) -> tuple[str, bool]:
            return self._run_tool(name, args, world, actions)

        try:
            stream = self.backend().reply(
                self.model,
                self.system_prompt(),
                self.history[-12:],
                self._context(world),
                said,
                TOOL_DEFS,
                run_tool,
                out,
            )
            first = True
            async for sentence in stream:
                if first:
                    self.last_latency = time.perf_counter() - started
                    first = False
                yield sentence
            self.status = "ready"
            self.last_error = None
        except LLMError as e:
            self._fail(e)
            raise
        except Exception as e:  # noqa: BLE001 - a backend bug must not kill the radio
            log.exception("AI reply failed")
            err = LLMError(f"AI reply failed: {e}")
            self._fail(err)
            raise err from e
        finally:
            if out.saw_lone_think:
                self._lone_think.add(self.model)
            if out.spoken:
                # Only what the driver heard: the conversation stops after MAX_REPLY_SENTENCES.
                self.history += [
                    {"role": "user", "content": said},
                    {"role": "assistant", "content": " ".join(out.spoken[:MAX_REPLY_SENTENCES])},
                ]
                self.history = self.history[-12:]

    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        """One-shot text (debriefs, the connection test). Raises LLMError."""
        try:
            text = await self.backend().complete(self.model, self.system_prompt(), prompt, max_tokens)
        except LLMError as e:
            self._fail(e)
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("AI completion failed")
            err = LLMError(f"AI request failed: {e}")
            self._fail(err)
            raise err from e
        self.status = "ready"
        self.last_error = None
        return clean_for_radio(text)

    async def list_models(self) -> list[str]:
        return await self.backend().list_models()

    async def check(self) -> None:
        """A free connection check (lists models) so the status is honest before the first question."""
        if not self.available:
            return
        self.status = "checking"
        try:
            models = await self.backend().list_models()
        except LLMError as e:
            low = str(e).lower()
            if "rejected" in low or "credit" in low or "can't reach" in low or "too long" in low:
                self._fail(e)
            else:
                self.status = "unverified"  # some services don't list models; the first answer will tell
            log.info("AI check (%s): %s", self.describe(), e)
            return
        except Exception as e:  # noqa: BLE001
            self.status = "unverified"
            log.info("AI check (%s) failed: %s", self.describe(), e)
            return
        self.status = "ready"
        self.last_error = None
        hint = self.unknown_model_hint(models)
        if hint:
            self.status = "unverified"
            self.last_error = hint
        log.info("AI ready: %s%s", self.describe(), f" ({hint})" if hint else "")

    def unknown_model_hint(self, models: list[str]) -> str | None:
        """If the configured model isn't one the service lists, say so and suggest the closest ones."""
        import difflib

        model = self.model
        if not model or not models or self.provider.kind == "anthropic":
            return None  # Anthropic aliases aren't always listed; local servers use whatever is loaded
        lower = {m.lower(): m for m in models}
        if model.lower() in lower:
            return None
        close = difflib.get_close_matches(model.lower(), list(lower), n=3, cutoff=0.4)
        tip = f" Closest: {', '.join(lower[c] for c in close)}." if close else ""
        return f"{self.provider.label} doesn't list a model called '{model}'.{tip} Pick one with List models."

    def _run_tool(self, name: str, args: dict[str, Any], world: World, actions: Actions) -> tuple[str, bool]:
        try:
            if name == "radio_preferences":
                done = []
                if isinstance(args.get("verbosity"), str) and args["verbosity"] in ("quiet", "normal", "chatty"):
                    actions.set_verbosity(args["verbosity"])
                    done.append(f"verbosity {args['verbosity']}")
                gap_every = args.get("gap_every_laps")
                if isinstance(gap_every, (int, float)) and not isinstance(gap_every, bool):
                    actions.set_gap_every(max(1, min(10, int(gap_every))))
                    done.append("gap frequency")
                if isinstance(args.get("silence_minutes"), (int, float)):
                    minutes = float(args["silence_minutes"])
                    if minutes > 0:
                        actions.mute(minutes)
                    else:
                        actions.unmute()
                    done.append("radio silence" if minutes > 0 else "radio back on")
                result: Any = {"ok": True, "changed": done}
            elif name == "remember":
                note = args.get("note")
                if not isinstance(note, str) or not note.strip():
                    raise ValueError("note must be a non-empty string")
                actions.remember(note.strip())
                result = {"ok": True}
            else:
                fn = tools.TOOLS.get(name)
                if fn is None:
                    raise ValueError(f"unknown tool {name}")
                kwargs: dict[str, Any] = {}
                for k, v in args.items():
                    if k == "name" and isinstance(v, str):
                        kwargs[k] = v
                    elif k in ("top", "last") and isinstance(v, (int, float)) and not isinstance(v, bool):
                        kwargs[k] = int(v)
                result = fn(world, **kwargs)
            return json.dumps(result, default=str), False
        except Exception as e:  # noqa: BLE001
            log.warning("tool %s failed: %s", name, e)
            return f"error: {e}", True
