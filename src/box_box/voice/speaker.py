"""The engineer's mouth: radio call -> spoken text -> synthesized voice -> radio effect -> speakers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from box_box.bus import Bus
from box_box.config import Settings
from box_box.engineer.radio import RadioCall
from box_box.voice import fx
from box_box.voice.normalize import for_speech
from box_box.voice.player import AudioPlayer
from box_box.voice.tts import TTS

log = logging.getLogger(__name__)

SYNTH_TIMEOUT_S = 12.0


class Speaker:
    def __init__(self, tts: TTS, player: AudioPlayer, settings: Callable[[], Settings], bus: Bus | None = None) -> None:
        self.tts = tts
        self.player = player
        self._settings = settings
        self.bus = bus or Bus()
        self.speaking: RadioCall | None = None
        self._cut = False
        self.spoken = 0

    def _voice(self) -> tuple[str, float]:
        v = self._settings().voice
        return v.voice, v.speed

    def prefetch(self, calls: list[RadioCall]) -> None:
        """Start synthesizing queued calls now, so they play instantly when the director picks them."""
        if self.tts.active == "off":
            return
        voice, speed = self._voice()
        for c in calls:
            self.tts.submit(for_speech(c.text), voice, speed)

    def stop(self) -> None:
        """Barge-in: cut the engineer off mid-sentence."""
        self._cut = True
        self.player.stop()

    async def say(self, call: RadioCall) -> bool:
        """Speak one call. Returns True if it played to the end."""
        s = self._settings()
        self.speaking = call
        self._cut = False
        self.bus.publish(
            "radio", id=call.id, text=call.text, priority=call.priority.name, category=call.category, source=call.source
        )
        try:
            if s.voice.engine == "off" or self.tts.active == "off":
                await asyncio.sleep(min(4.0, 0.06 * len(call.text)))  # pace text-only mode like speech
                return True
            voice, speed = self._voice()
            future = self.tts.submit(for_speech(call.text), voice, speed)
            try:
                samples, sr = await asyncio.wait_for(asyncio.wrap_future(future), SYNTH_TIMEOUT_S)
            except Exception as e:  # noqa: BLE001
                log.error("couldn't synthesize %r: %s", call.text, e)
                self.bus.publish("warning", message=f"Voice failed: {e}")
                return False
            if self._cut or len(samples) == 0:
                return False
            audio = fx.radio(samples, sr, s.voice.radio_effect, beeps=s.voice.beeps)
            self.player.volume = s.voice.volume
            done = await asyncio.wrap_future(self.player.play(audio, sr))
            self.spoken += 1
            return bool(done) and not self._cut
        finally:
            self.speaking = None
            self.bus.publish("radio_done", id=call.id)
