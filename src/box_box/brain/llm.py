"""Claude as the engineer's brain for open conversation: streaming, tool use, short radio-style replies."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from box_box.brain import tools
from box_box.brain.intents import Actions
from box_box.config import Settings
from box_box.state.model import World

log = logging.getLogger(__name__)

PERSONALITY = {
    "calm": "Calm, precise and supportive, like the best F1 race engineers. You only raise your tone for big moments.",
    "fired_up": "Energetic and passionate. You get properly excited about overtakes and results, but stay clear and short.",
    "dry": "Calm with a dry, deadpan sense of humour. Occasionally a wry one-liner, never at the expense of clarity.",
}

SYSTEM = """You are {engineer}, race engineer for {driver} in their EA Sports F1 25 career, racing against AI drivers. \
You talk to {driver} over team radio while they drive. Everything you write is read aloud by text-to-speech.

How you talk:
- Real F1 team radio: one or two short sentences, usually under 20 words. Lead with the answer. No filler, \
no "great question", no sign-offs.
- Plain spoken English only. No lists, markdown, emoji, brackets or stage directions.
- Numbers the way engineers say them: gaps like "1.2", deltas like "three tenths", positions like "P4", \
lap times like "1:26.4", percentages as "40 percent".
- Use {driver}'s name sparingly, for emphasis.
- {personality}

Facts:
- Never invent data. Use the live snapshot in the message or call a tool. If the data isn't there, say \
"Stand by" or that you don't have it.
- You can't touch the car or the game. If {driver} wants to pit or change something, confirm it and tell them \
to do it on the MFD ("Copy, box this lap, confirm on the MFD").
- If {driver} asks you to talk more or less, change how often you give gaps, go quiet for a while, or remember \
something about them, use the radio_preferences or remember tools and confirm in a few words.
- If you can't understand what {driver} said, ask them to say again.
- You are the engineer on the pit wall, in character. Don't mention being an AI, tools or telemetry packets.

What you know about {driver}:
{profile}"""

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "session_info",
        "description": "Track, session type, weather, safety car, laps or time remaining, the driver's position and the pit window.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "gaps",
        "description": "Race: gaps to the cars directly ahead and behind and to the leader. Practice/quali: the driver's best lap vs the fastest.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "standings",
        "description": "Current order with each car's tyres, tyre age, pit stops and best/last lap.",
        "input_schema": {
            "type": "object",
            "properties": {"top": {"type": "integer", "description": "How many positions, default 10."}},
        },
    },
    {
        "name": "driver",
        "description": "Everything about one rival: position, gap to us, tyres, pit stops, best lap.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Driver surname, e.g. 'Leclerc'."}},
            "required": ["name"],
        },
    },
    {
        "name": "tyres",
        "description": "Our tyres: compound, age, wear per corner, wear rate, laps of life left, temperatures, pressures.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "car_health",
        "description": "Our damage (wings, floor, diffuser, sidepods, engine, gearbox), faults, brake and engine temps.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "fuel",
        "description": "Fuel on board and, in a race, the margin at the finish.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "energy",
        "description": "Battery level, deploy mode, overtake mode availability, active aero mode (2026 cars).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "weather",
        "description": "Current weather and the forecast.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lap_times",
        "description": "Recent lap and sector times for us or a named driver.",
        "input_schema": {
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
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "radio_preferences",
        "description": "Change how the engineer talks: verbosity, how often to give gaps, or radio silence for some minutes.",
        "input_schema": {
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
        "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]},
    },
]
for _tool in TOOL_DEFS:
    _tool["eager_input_streaming"] = True


class LLMError(RuntimeError):
    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal  # bad key, no access: stop trying until settings change


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Pull complete sentences off the front of a streaming buffer."""
    parts = re.split(r"(?<=[.!?])\s+", buffer)
    if len(parts) == 1:
        return [], buffer
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


def clean_for_radio(text: str) -> str:
    text = re.sub(r"[*_#`>\[\]]", "", text)
    text = re.sub(r"\s*\n+\s*", " ", text)
    return text.strip()


class ClaudeBrain:
    def __init__(self, settings: Callable[[], Settings], profile: Callable[[], str]) -> None:
        self._settings = settings
        self._profile = profile
        self._client = None
        self._client_key = ""
        self.history: list[dict[str, Any]] = []
        self.status = "not configured"
        self.last_error: str | None = None
        self.disabled_reason: str | None = None

    @property
    def available(self) -> bool:
        s = self._settings()
        return bool(s.ai.enabled and s.api_key and not self.disabled_reason)

    def reset_errors(self) -> None:
        self.disabled_reason = None

    def _get_client(self):  # type: ignore[no-untyped-def]
        key = self._settings().api_key
        if self._client is None or key != self._client_key:
            import anthropic

            self._client = anthropic.AsyncAnthropic(
                api_key=key, timeout=anthropic.Timeout(20.0, connect=5.0), max_retries=1
            )
            self._client_key = key
            self.disabled_reason = None
        return self._client

    def system_prompt(self) -> str:
        s = self._settings()
        return SYSTEM.format(
            engineer=s.engineer.name,
            driver=s.driver.name,
            personality=PERSONALITY.get(s.engineer.personality, PERSONALITY["calm"]),
            profile=self._profile() or "- Nothing yet. This is one of your first sessions together.",
        )

    async def reply(self, transcript: str, world: World, actions: Actions) -> AsyncIterator[str]:
        """Stream the engineer's answer, sentence by sentence."""
        import anthropic

        s = self._settings()
        client = self._get_client()
        user_turn = {
            "role": "user",
            "content": [
                {"type": "text", "text": f"[Live pit wall: {tools.snapshot(world)}]"},
                {"type": "text", "text": f"{s.driver.name}: {transcript}"},
            ],
        }
        messages = [*self.history[-12:], user_turn]
        spoken: list[str] = []
        try:
            for _round in range(4):
                buffer = ""
                async with client.messages.stream(
                    model=s.ai.model,
                    max_tokens=1024,
                    system=[{"type": "text", "text": self.system_prompt(), "cache_control": {"type": "ephemeral"}}],
                    tools=TOOL_DEFS,
                    messages=messages,
                    output_config={"effort": "low"},
                ) as stream:
                    async for event in stream:
                        if event.type == "text":
                            buffer += event.text
                            sentences, buffer = split_sentences(buffer)
                            for sentence in sentences:
                                sentence = clean_for_radio(sentence)
                                if sentence:
                                    spoken.append(sentence)
                                    yield sentence
                    final = await stream.get_final_message()
                tail = clean_for_radio(buffer)
                if tail:
                    spoken.append(tail)
                    yield tail
                if final.stop_reason == "refusal":
                    yield "Say again?"
                    return
                tool_uses = [b for b in final.content if b.type == "tool_use"]
                if final.stop_reason != "tool_use" or not tool_uses:
                    break  # end_turn, max_tokens: nothing to run
                messages.append({"role": "assistant", "content": final.content})
                messages.append({"role": "user", "content": [self._run_tool(b, world, actions) for b in tool_uses]})
            self.status = "ready"
            self.last_error = None
        except anthropic.AuthenticationError as e:
            self.disabled_reason = "The API key was rejected. Check it in Settings > AI."
            raise LLMError(self.disabled_reason, fatal=True) from e
        except anthropic.PermissionDeniedError as e:
            self.disabled_reason = "This API key can't use that model. Check your Anthropic console."
            raise LLMError(self.disabled_reason, fatal=True) from e
        except anthropic.NotFoundError as e:
            self.disabled_reason = f"Model {s.ai.model} wasn't found. Pick another model in Settings > AI."
            raise LLMError(self.disabled_reason, fatal=True) from e
        except anthropic.RateLimitError as e:
            self.status = "rate limited"
            raise LLMError("Rate limited by the Anthropic API.") from e
        except anthropic.BadRequestError as e:
            msg = getattr(e, "message", str(e))
            if "credit" in msg.lower() or "billing" in msg.lower():
                self.disabled_reason = "Your Anthropic account is out of credit."
                raise LLMError(self.disabled_reason, fatal=True) from e
            raise LLMError(f"Request rejected: {msg}") from e
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            self.status = "offline"
            raise LLMError("Can't reach the Anthropic API (offline?).") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}.") from e
        finally:
            if spoken:
                self.history += [
                    {"role": "user", "content": f"{s.driver.name}: {transcript}"},
                    {"role": "assistant", "content": " ".join(spoken)},
                ]
                self.history = self.history[-12:]

    def _run_tool(self, block: Any, world: World, actions: Actions) -> dict[str, Any]:
        args = block.input if isinstance(block.input, dict) else {}
        try:
            if block.name == "radio_preferences":
                done = []
                if isinstance(args.get("verbosity"), str) and args["verbosity"] in ("quiet", "normal", "chatty"):
                    actions.set_verbosity(args["verbosity"])
                    done.append(f"verbosity {args['verbosity']}")
                if isinstance(args.get("gap_every_laps"), int):
                    actions.set_gap_every(max(1, min(10, args["gap_every_laps"])))
                    done.append("gap frequency")
                if isinstance(args.get("silence_minutes"), (int, float)):
                    minutes = float(args["silence_minutes"])
                    if minutes > 0:
                        actions.mute(minutes)
                    else:
                        actions.unmute()
                    done.append("radio silence" if minutes > 0 else "radio back on")
                result: Any = {"ok": True, "changed": done}
            elif block.name == "remember":
                note = args.get("note")
                if not isinstance(note, str) or not note.strip():
                    raise ValueError("note must be a non-empty string")
                actions.remember(note.strip())
                result = {"ok": True}
            else:
                fn = tools.TOOLS.get(block.name)
                if fn is None:
                    raise ValueError(f"unknown tool {block.name}")
                kwargs = {k: v for k, v in args.items() if k in ("name", "top", "last") and isinstance(v, (str, int))}
                result = fn(world, **kwargs)
            return {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, default=str)}
        except Exception as e:  # noqa: BLE001
            log.warning("tool %s failed: %s", block.name, e)
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"error: {e}", "is_error": True}

    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        """One-shot text (debriefs). Raises LLMError on failure."""
        import anthropic

        s = self._settings()
        client = self._get_client()
        try:
            msg = await client.messages.create(
                model=s.ai.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": self.system_prompt(), "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low"},
            )
        except anthropic.APIError as e:
            raise LLMError(str(e)) from e
        if msg.stop_reason == "refusal":
            raise LLMError("refused")
        return clean_for_radio(" ".join(b.text for b in msg.content if b.type == "text"))
