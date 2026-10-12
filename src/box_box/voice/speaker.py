"""The engineer's mouth: radio call -> spoken text -> synthesized voice -> radio effect -> speakers."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Callable

import numpy as np

from box_box.bus import Bus
from box_box.config import Settings
from box_box.engineer.radio import Priority, RadioCall
from box_box.voice import fx
from box_box.voice.normalize import for_speech
from box_box.voice.player import AudioPlayer
from box_box.voice.tts import TTS

log = logging.getLogger(__name__)

SYNTH_TIMEOUT_S = 12.0
# How often a two-sentence call is said with the key held between the sentences (he glances at the
# screen, then carries on): dead air with the carrier still open. Never on urgent calls.
HOLD_CHANCE = {"off": 0.0, "subtle": 0.2, "human": 0.35}
HOLD_GAP_S = (0.35, 0.8)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def hold_split(call: RadioCall, natural_speech: str) -> tuple[list[str], float]:
    """The pieces to synthesize and the held-key gap between them. Decided from the call itself, so the
    prefetch and the playback agree without sharing state."""
    chance = HOLD_CHANCE.get(natural_speech, 0.0)
    pieces = _SENTENCE_BREAK.split(call.text.strip(), maxsplit=1)
    if (
        not chance
        or call.priority == Priority.SAFETY
        or call.source != "engineer"
        or len(pieces) < 2
        or len(pieces[0]) < 8
        or len(pieces[1]) < 8
    ):
        return [call.text], 0.0
    rng = random.Random(f"{call.id}:{call.text}")
    if rng.random() >= chance:
        return [call.text], 0.0
    return pieces, rng.uniform(*HOLD_GAP_S)


def reply_part(call: RadioCall) -> int | None:
    """Which sentence of a spoken reply this is ("reply:<id>:<n>"), or None for a standalone call."""
    parts = call.key.split(":")
    if len(parts) == 3 and parts[0] == "reply" and parts[2].isdigit():
        return int(parts[2])
    return None


class Speaker:
    def __init__(self, tts: TTS, player: AudioPlayer, settings: Callable[[], Settings], bus: Bus | None = None) -> None:
        self.tts = tts
        self.player = player
        self._settings = settings
        self.bus = bus or Bus()
        self.speaking: RadioCall | None = None
        self.has_followup: Callable[[RadioCall], bool] = lambda call: False  # more of the same reply queued?
        self._generation = 0  # bumped by stop(); a call whose generation is stale must not play
        self.spoken = 0

    def _voice(self) -> tuple[str, float]:
        v = self._settings().voice
        return v.voice, v.speed

    def prefetch(self, calls: list[RadioCall]) -> None:
        """Start synthesizing queued calls now, so they play instantly when the director picks them."""
        if self.tts.active == "off":
            return
        voice, speed = self._voice()
        natural = self._settings().engineer.natural_speech
        for c in calls:
            for piece in hold_split(c, natural)[0]:
                self.tts.submit(for_speech(piece), voice, speed, urgent=False)

    def stop(self) -> None:
        """Barge-in: cut the engineer off mid-sentence."""
        self._generation += 1
        self.player.stop()

    async def say(self, call: RadioCall) -> bool:
        """Speak one call. Returns True if it played to the end."""
        s = self._settings()
        self.speaking = call
        generation = self._generation
        self.bus.publish(
            "radio", id=call.id, text=call.text, priority=call.priority.name, category=call.category, source=call.source
        )
        try:
            if s.voice.engine == "off" or self.tts.active == "off":
                await asyncio.sleep(min(4.0, 0.06 * len(call.text)))  # pace text-only mode like speech
                return True
            voice, speed = self._voice()
            pieces, gap_s = hold_split(call, s.engineer.natural_speech)
            futures = [self.tts.submit(for_speech(p), voice, speed) for p in pieces]
            try:
                clips = [await asyncio.wait_for(asyncio.wrap_future(f), SYNTH_TIMEOUT_S) for f in futures]
                sr = clips[0][1]
                joined: list[np.ndarray] = []
                for n, (clip, clip_sr) in enumerate(clips):
                    if n:
                        joined.append(fx.hold_gap(sr, gap_s))
                    joined.append(fx.resample(clip, clip_sr, sr))
                samples = np.concatenate(joined) if len(joined) > 1 else clips[0][0]
            except TimeoutError:
                log.error("voice took over %ss for %r", SYNTH_TIMEOUT_S, call.text)
                self.bus.publish(
                    "warning",
                    message="The voice is taking too long. Your CPU may be busy; try the Windows voice in Settings > Voice.",
                )
                return False
            except Exception as e:  # noqa: BLE001
                log.error("couldn't synthesize %r: %s", call.text, e)
                self.bus.publish("warning", message=f"Voice failed: {e or type(e).__name__}")
                return False
            if generation != self._generation or len(samples) == 0:
                return False
            try:
                part = reply_part(call)
                audio = fx.radio(
                    samples,
                    sr,
                    s.voice.radio_effect,
                    beeps=s.voice.beeps,
                    open_beep=part is None or part == 0,
                    close_beep=part is None or not self.has_followup(call),
                )
            except Exception:  # noqa: BLE001 - a broken effect must never silence the engineer
                log.exception("radio effect failed; playing the dry voice")
                audio = samples
            self.player.volume = s.voice.volume
            done = await asyncio.wrap_future(self.player.play(audio, sr))
            self.spoken += 1
            return bool(done) and generation == self._generation
        finally:
            if self.speaking is call:  # an interrupting call may already have taken over
                self.speaking = None
            self.bus.publish("radio_done", id=call.id)
