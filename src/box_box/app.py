"""The engine: telemetry in, radio out, and the driver's voice in between.

One asyncio loop owns the world state. Hardware and slow work (audio devices, TTS, speech recognition,
controller polling, downloads) live on their own threads and report back here. Every subsystem reports
a status the UI can show, and none of them failing stops the others.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from box_box import config
from box_box.brain.conversation import Conversation
from box_box.brain.llm import Brain
from box_box.bus import Bus
from box_box.engineer.base import Context
from box_box.engineer.detectors import all_detectors
from box_box.engineer.director import RadioDirector
from box_box.engineer.radio import Phrasebook, Priority, RadioCall, lap_time
from box_box.memory.store import Memory
from box_box.state.model import LapCompleted, SessionChanged, World
from box_box.telemetry import names
from box_box.telemetry.autorecord import AutoRecorder
from box_box.telemetry.packets import Decoder, PacketId
from box_box.telemetry.sources import PortUnavailable, bind_udp, file_source, udp_source
from box_box.voice.cloud import cloud_engines
from box_box.voice.mic import Microphone
from box_box.voice.models import kokoro_files
from box_box.voice.player import AudioPlayer
from box_box.voice.ptt import Binding, PushToTalk
from box_box.voice.speaker import Speaker
from box_box.voice.stt import SpeechToText
from box_box.voice.tts import TTS

log = logging.getLogger(__name__)

RADIO_POLL_S = 0.05
PTT_STUCK_S = 30.0  # held longer than this: the button is stuck, not the driver talking
MIN_PTT_HOLD_S = 0.3
STATUS_EVERY_S = 0.5
CONNECTED_WITHIN_S = 3.0
_reply_ids = itertools.count(1)


@dataclass
class TelemetryStats:
    packets: int = 0
    last_at: float = 0.0
    rate: float = 0.0
    _window_start: float = 0.0
    _window_count: int = 0

    def hit(self) -> None:
        now = time.monotonic()
        self.packets += 1
        self.last_at = now
        self._window_count += 1
        if now - self._window_start >= 1.0:
            self.rate = self._window_count / (now - self._window_start) if self._window_start else 0.0
            self._window_start, self._window_count = now, 0

    @property
    def connected(self) -> bool:
        return time.monotonic() - self.last_at < CONNECTED_WITHIN_S


class Engine:
    def __init__(
        self,
        store: config.SettingsStore,
        *,
        player: AudioPlayer | None = None,
        tts: TTS | None = None,
        stt: SpeechToText | None = None,
        mic: Microphone | None = None,
        memory: Memory | None = None,
        enable_ptt: bool = True,
        enable_udp: bool = True,
        downloader=kokoro_files,  # type: ignore[no-untyped-def]
    ) -> None:
        self.store = store
        self.bus = Bus()
        self.decoder = Decoder(Decoder.DEFAULT_WANTED)
        self.world = World()
        self.detectors = all_detectors()
        self.say = Phrasebook(self.settings.engineer.personality)
        self.director = RadioDirector(lambda: self.settings)
        self.tts = tts or TTS()
        if tts is None:
            self.tts.engines.update(cloud_engines(lambda: self.settings))
        self.player = player or AudioPlayer()
        self.speaker = Speaker(self.tts, self.player, lambda: self.settings, self.bus)
        self.speaker.has_followup = self._reply_continues
        self.mic = mic or Microphone()
        self.stt = stt or SpeechToText()
        self.memory = memory or Memory()
        self.brain = Brain(lambda: self.settings, self.memory.driver_profile, self._recent_radio)
        self.conversation = Conversation(self.brain, self, lambda: self.settings, self.bus)
        self.ptt = PushToTalk(self._ptt_from_thread) if enable_ptt else None
        self.recorder = AutoRecorder(self.settings.recordings_dir(), self.settings.telemetry.keep_recordings)
        self.downloader = downloader
        self.enable_udp = enable_udp
        self.stats = TelemetryStats()
        self.port_error: str | None = None
        self.replaying: str | None = None
        self.replay_speed = 1.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._udp_task: asyncio.Task | None = None
        self._replay_task: asyncio.Task | None = None
        self._speak_task: asyncio.Task | None = None
        self._session_db_id: int | None = None
        self._ended_sessions: set[int] = set()
        self._tts_wanted: str | None = None
        self._ptt_pressed_at = 0.0
        self._ai_check: asyncio.Task | None = None
        self.started_at = time.time()
        store.on_change(self._settings_changed_from_any_thread)

    @property
    def settings(self) -> config.Settings:
        return self.store.settings

    # ================================================================================================
    # lifecycle
    # ================================================================================================

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        s = self.settings
        self._open_udp()
        self.player.open(s.voice.output_device)
        self.mic.open(s.mic.input_device)
        self._setup_voice()
        self._load_stt()
        self._configure_ptt()
        self._check_ai()
        self._tasks += [
            asyncio.create_task(self._radio_loop(), name="radio"),
            asyncio.create_task(self._status_loop(), name="status"),
            asyncio.create_task(self._housekeeping(), name="housekeeping"),
        ]
        log.info("Box Box engine started")

    async def stop(self) -> None:
        for t in [*self._tasks, self._udp_task, self._replay_task, self._speak_task, self._ai_check]:
            if t is not None and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        self._tasks.clear()
        self.recorder.close()
        if self.ptt:
            self.ptt.stop()
        self.player.close()
        self.mic.close()
        self.tts.shutdown()
        await self.brain.aclose()
        self.memory.close()
        log.info("Box Box engine stopped")

    # ================================================================================================
    # telemetry
    # ================================================================================================

    def _open_udp(self) -> None:
        if not self.enable_udp:
            return
        if self._udp_task and not self._udp_task.done():
            self._udp_task.cancel()
        port = self.settings.telemetry.port
        host = "0.0.0.0" if self.settings.telemetry.listen_on_network else "127.0.0.1"
        try:
            sock = bind_udp(port, host)
        except PortUnavailable as e:
            self.port_error = (
                f"UDP port {port} is in use or blocked ({e.cause}). Close other telemetry apps (SimHub, Crew Chief...) "
                f"or pick another port in Settings > Telemetry and set the same port in F1 25."
            )
            log.error(self.port_error)
            return
        self.port_error = None
        self._udp_task = asyncio.create_task(self._udp_loop(sock), name="udp")
        log.info("listening for F1 25 telemetry on UDP %d", port)

    async def _udp_loop(self, sock) -> None:  # type: ignore[no-untyped-def]
        async for data in udp_source(sock):
            if self.replaying:
                continue  # live telemetry paused while a recording plays
            self.ingest(data, live=True)

    def ingest(self, data: bytes, live: bool = False) -> None:
        """One datagram from the game or a recording."""
        try:
            self.stats.hit()
            if live and self.settings.telemetry.auto_record and len(data) >= 29:
                uid = int.from_bytes(data[7:15], "little")
                # This packet isn't applied yet: the world may still hold the previous session (that's how a
                # race recording ended up named after the FP1 before it).
                s = self.world.session if self.world.session_uid == uid else None
                self.recorder.feed(data, uid, s.track_id if s else None, s.session_type if s else None)
            packet = self.decoder.decode(data)
            if packet is None:
                return
            if packet.id == PacketId.EVENT and packet.data.code == "BUTN":  # type: ignore[union-attr]
                if self.ptt:
                    self.ptt.game_buttons(int(packet.data.detail.get("buttons", 0)))  # type: ignore[union-attr]
                return
            self.world.apply(packet)
            self.world.last_packet_time = time.monotonic()
            if packet.id == PacketId.PARTICIPANTS:
                self._update_vocab()
            if packet.id == PacketId.LAP_DATA:
                self._tick()
        except Exception:
            log.exception("telemetry processing failed")  # keep going: one bad packet mustn't stop the radio

    def _tick(self) -> None:
        world = self.world
        changes = world.drain_changes()
        events = world.drain_events()
        self._lifecycle(changes, events)
        self.say.personality = self.settings.engineer.personality
        ctx = Context(world, changes, events, self.settings, self.say)
        calls: list[RadioCall] = []
        for detector in self.detectors:
            calls += detector.update(ctx)
        self.director.on_telemetry(world)
        if calls:
            for c in calls:
                self.bus.publish("queued", text=c.text, priority=c.priority.name, category=c.category)
            self.director.offer(calls)
            self.speaker.prefetch(self.director.pending()[:2])

    def _lifecycle(self, changes: list, events: list) -> None:
        world = self.world
        for change in changes:
            if isinstance(change, SessionChanged):
                self.director.clear()
                self.brain.history.clear()
                s = world.session
                if s is not None:
                    self._session_db_id = self.memory.start_session(
                        world.session_uid, names.track_name(s.track_id), names.session_name(s.session_type)
                    )
                    self._update_vocab()
                    self.bus.publish(
                        "session", track=names.track_name(s.track_id), session=names.session_name(s.session_type)
                    )
            elif isinstance(change, LapCompleted) and change.car_index == world.player_index and self._session_db_id:
                self.memory.record_lap(self._session_db_id, change.record)
        if any(e.code == "SEND" for e in events) and world.session_uid not in self._ended_sessions:
            self._ended_sessions.add(world.session_uid)
            self._end_session()

    def _update_vocab(self) -> None:
        """Teach speech recognition this session's driver names and track (so 'Hülkenberg' isn't 'hulk and burg')."""
        s = self.world.session
        drivers = sorted({c.name for c in self.world.cars if c.participant and c.participant.name})
        vocab = "Drivers: " + ", ".join(drivers) + "." if drivers else ""
        if s is not None:
            vocab += f" Track: {names.track_name(s.track_id)}."
        self.stt.extra_vocab = vocab

    def _end_session(self) -> None:
        me = self.world.player
        if self._session_db_id and me and me.lap:
            self.memory.end_session(self._session_db_id, me.session_best_ms, me.lap.position)
        if self.settings.telemetry.auto_record and not self.replaying:
            self.recorder.close()
        if not self.world.is_time_trial:
            self._spawn(self._debrief())

    async def _debrief(self) -> None:
        await asyncio.sleep(6)  # let the session-end call land first
        text = await self.conversation.debrief(self.world)
        if text:
            self._offer_reply(text, next(_reply_ids), 0)

    # ---- replay -----------------------------------------------------------------------------------

    def start_replay(self, path: Path, speed: float = 1.0) -> None:
        self.stop_replay()
        self.replaying = path.name
        self.replay_speed = speed
        self.world = World()  # a recording is its own session
        self._replay_task = asyncio.create_task(self._replay(path, speed), name="replay")
        self.bus.publish("replay", state="started", name=path.name)

    def stop_replay(self) -> None:
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
        self._replay_task = None
        if self.replaying:
            self.replaying = None
            self.world = World()
            self.director.clear()
            self.speaker.stop()
            self.bus.publish("replay", state="stopped")

    async def _replay(self, path: Path, speed: float) -> None:
        try:
            async for data in file_source(path, speed):
                self.ingest(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("replay of %s failed: %s", path.name, e)
            self.bus.publish("warning", message=f"Couldn't play {path.name}: {e}")
        finally:
            if self.replaying == path.name:
                self.replaying = None
                self.bus.publish("replay", state="finished", name=path.name)

    # ================================================================================================
    # radio out
    # ================================================================================================

    async def _radio_loop(self) -> None:
        while True:
            try:
                decision = self.director.poll(self.world, self.speaker.speaking)
                if decision is not None:
                    if decision.interrupt:
                        self.speaker.stop()
                    self.speaker.speaking = decision.call
                    if decision.call.key.startswith("ask:"):
                        self.conversation.expect_feedback(decision.call.key.split(":", 1)[1])
                    self.memory.log_radio(self._session_db_id, "engineer", decision.call.text)
                    self._speak_task = asyncio.create_task(self.speaker.say(decision.call))
            except Exception:
                log.exception("radio loop error")
            await asyncio.sleep(RADIO_POLL_S)

    def _offer_reply(self, sentence: str, reply_id: int, index: int) -> None:
        now = self.world.session_time
        call = RadioCall(f"reply:{reply_id}:{index}", Priority.STRATEGY, sentence, now, now + 30, source="reply")
        self.director.offer([call])
        self.speaker.prefetch([call])

    def _check_ai(self) -> None:
        if self._loop is None:
            return
        if self._ai_check and not self._ai_check.done():
            self._ai_check.cancel()
        self._ai_check = self._loop.create_task(self.brain.check(), name="ai-check")

    def _reply_continues(self, call: RadioCall) -> bool:
        """Is the next sentence of this reply already queued (so the radio stays keyed between them)?"""
        prefix = call.key.rsplit(":", 1)[0] + ":"
        return any(c.key.startswith(prefix) and c.key != call.key for c in self.director.pending())

    def _recent_radio(self) -> list[str]:
        """What the engineer said on his own in the last few minutes, for the AI's context."""
        cutoff = time.time() - 240
        return [
            e["text"]
            for e in list(self.bus.history)[-40:]
            if e["type"] == "radio" and e.get("source") == "engineer" and e["at"] >= cutoff
        ][-5:]

    def say_now(self, text: str) -> None:
        """Speak a line straight away (voice tests, onboarding)."""
        self._offer_reply(text, next(_reply_ids), 0)

    # ================================================================================================
    # the driver talking
    # ================================================================================================

    def _ptt_from_thread(self, pressed: bool) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_ptt, pressed)

    def on_ptt(self, pressed: bool) -> None:
        self.bus.publish("ptt", active=pressed)
        self.director.on_ptt(pressed)
        if pressed:
            self._ptt_pressed_at = time.monotonic()
            self.speaker.stop()
            self.mic.start_recording()
        else:
            audio = self.mic.stop_recording()
            if time.monotonic() - self._ptt_pressed_at < MIN_PTT_HOLD_S:
                self.director.release_hold()  # an accidental tap: ignore it rather than "say again?"
                return
            self._spawn(self._handle_utterance(audio))

    def _ptt_watchdog(self) -> None:
        """A button that reads as held for ages is stuck (wrong binding, a toggle left on, a bad axis).
        Holding the radio forever would make the engineer go silent, so let go and say why."""
        if not self.director.ptt_down or time.monotonic() - self._ptt_pressed_at < PTT_STUCK_S:
            return
        log.warning(
            "push-to-talk has been held for %.0f s; treating it as stuck", time.monotonic() - self._ptt_pressed_at
        )
        self.bus.publish(
            "warning",
            message="Push-to-talk seems stuck on (held for over 30 seconds), so the engineer couldn't talk. "
            "Check the button in Setup > Button, or switch the button mode to 'Hold to talk'.",
        )
        self.mic.stop_recording()
        self.director.on_ptt(False)
        self.director.release_hold()
        if self.ptt:
            self.ptt.force_release()

    async def _handle_utterance(self, audio) -> None:  # type: ignore[no-untyped-def]
        reply_id = next(_reply_ids)
        if not self.mic.ok:
            self._offer_reply(
                "I can't hear you, your microphone isn't working. Check the Diagnostics page.", reply_id, 0
            )
            return
        if not self.stt.ready:
            msg = (
                "Speech recognition is still loading, give me a minute."
                if "loading" in self.stt.status
                else ("Speech recognition isn't working. Check the Diagnostics page.")
            )
            self._offer_reply(msg, reply_id, 0)
            self.director.release_hold()
            return
        try:
            transcript = await asyncio.wrap_future(self.stt.transcribe_async(audio))
        except Exception as e:  # noqa: BLE001
            log.error("transcription failed: %s", e)
            self._offer_reply(self.say("didnt_catch", me=self.settings.driver.name), reply_id, 0)
            self.director.release_hold()
            return
        self.bus.publish(
            "transcript", text=transcript.text, seconds=round(transcript.seconds, 1), took=round(transcript.took, 2)
        )
        await self.respond(transcript.text, reply_id)

    async def respond(self, text: str, reply_id: int | None = None) -> None:
        """Answer the driver (spoken via push-to-talk, or typed in the UI)."""
        reply_id = reply_id or next(_reply_ids)
        if text.strip():
            self.memory.log_radio(self._session_db_id, "driver", text)
        index = 0
        try:
            async for sentence in self.conversation.handle(text, self.world):
                self._offer_reply(sentence, reply_id, index)
                index += 1
                self.director.release_hold()
        except Exception:
            log.exception("conversation failed")
            self._offer_reply("Radio's playing up. Say again?", reply_id, index)
        finally:
            self.director.release_hold()

    def _spawn(self, coro) -> None:  # type: ignore[no-untyped-def]
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)

    # ================================================================================================
    # intents.Actions: things the driver can change by voice
    # ================================================================================================

    def set_verbosity(self, level: str) -> None:
        self.store.update({"engineer": {"verbosity": level}})

    def mute(self, minutes: float) -> None:
        self.director.mute(minutes * 60)

    def unmute(self) -> None:
        self.director.muted_until = 0.0

    def set_gap_every(self, laps: int) -> None:
        self.store.update({"callouts": {"gap_every_laps": laps}})

    def set_callout(self, category: str, enabled: bool) -> None:
        if category in config.CalloutSettings.__dataclass_fields__:
            self.store.update({"callouts": {category: enabled}})

    def remember(self, note: str) -> None:
        self.memory.add_note("driver", note)

    def last_radio(self) -> str | None:
        """The engineer's last transmission, for "say again"."""
        for e in reversed(self.bus.history):
            if e["type"] == "radio" and e.get("source") != "reply":
                return e["text"]
        return None

    # ================================================================================================
    # subsystems & settings
    # ================================================================================================

    def _setup_voice(self) -> None:
        engine = self.settings.voice.engine
        self._tts_wanted = engine
        if engine == "kokoro" and not self.downloader.installed():
            self.downloader.start()  # housekeeping picks it up when it's done
            kokoro = self.tts.engines.get("kokoro")
            if kokoro is not None and getattr(kokoro, "available_model", lambda: None)():
                self._choose_voice("kokoro")  # the older voice model works meanwhile; upgraded when the download ends
            else:
                self._choose_voice("windows", status="downloading the voice (first run only)")
            return
        self._choose_voice(engine)

    def _choose_voice(self, engine: str, status: str | None = None) -> None:
        """Loading a neural voice takes a couple of seconds: do it off the event loop."""
        self.tts.status = f"loading the {engine} voice"

        def load() -> None:
            self.tts.choose(engine)
            if status:
                self.tts.status = status

        threading.Thread(target=load, name="voice-load", daemon=True).start()

    def _load_stt(self) -> None:
        m = self.settings.mic
        self.stt.load_async(m.stt_model, m.stt_device)

    def _configure_ptt(self) -> None:
        if not self.ptt:
            return
        p = self.settings.ptt
        binding = Binding(p.kind, p.device_name, p.device_guid, p.button, p.key, p.udp_action)
        self.ptt.configure(binding, p.mode)
        self.ptt.start()

    def _settings_changed_from_any_thread(self, old: config.Settings, new: config.Settings) -> None:
        if self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._apply_settings(old, new)
        else:
            self._loop.call_soon_threadsafe(self._apply_settings, old, new)

    def _apply_settings(self, old: config.Settings, new: config.Settings) -> None:
        try:
            if old.voice.output_device != new.voice.output_device:
                self.player.open(new.voice.output_device)
            cloud_changed = (
                old.voice.cloud_voice,
                old.voice.style,
                old.voice.elevenlabs_key,
                old.ai.openai_key,
                old.ai.gemini_key,
            ) != (
                new.voice.cloud_voice,
                new.voice.style,
                new.voice.elevenlabs_key,
                new.ai.openai_key,
                new.ai.gemini_key,
            )
            if cloud_changed:
                self.tts.clear_cache()  # cached lines were spoken with the old voice or style
            if old.voice.engine != new.voice.engine or (
                cloud_changed and new.voice.engine in ("openai", "elevenlabs", "gemini")
            ):
                self._setup_voice()
            if old.mic.input_device != new.mic.input_device:
                self.mic.open(new.mic.input_device)
            if (old.mic.stt_model, old.mic.stt_device) != (new.mic.stt_model, new.mic.stt_device):
                self.stt._model = None
                self._load_stt()
            if old.ptt != new.ptt:
                self._configure_ptt()
            if (old.telemetry.port, old.telemetry.listen_on_network) != (
                new.telemetry.port,
                new.telemetry.listen_on_network,
            ):
                self._open_udp()
            if (
                old.telemetry.recordings_dir != new.telemetry.recordings_dir
                or old.telemetry.keep_recordings != new.telemetry.keep_recordings
            ):
                self.recorder.close()
                self.recorder = AutoRecorder(new.recordings_dir(), new.telemetry.keep_recordings)
            if old.ai != new.ai:
                self.brain.reset_errors()
                self._check_ai()
            self.bus.publish("settings", settings=new.to_dict(redact=True))
        except Exception:
            log.exception("applying settings failed")

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                self.recorder.tick()
                if self.port_error and self.enable_udp:
                    self._open_udp()  # the other app may have closed
                if (
                    self._tts_wanted == "kokoro"
                    and self.tts.active != "kokoro"
                    and self.downloader.installed()
                    and not self.tts.status.startswith("loading")
                ):
                    self._choose_voice("kokoro")
                kokoro = self.tts.engines.get("kokoro")
                if self.tts.active == "kokoro" and getattr(kokoro, "upgradable", False):
                    log.info("switching to the full-quality voice model")
                    kokoro.reload()  # type: ignore[union-attr]
                    self.tts.clear_cache()
                    self._choose_voice("kokoro")
                if self.player.error and "disconnected" in self.player.error:
                    self.player.open(self.settings.voice.output_device)
                self._ptt_watchdog()
            except Exception:
                log.exception("housekeeping failed")

    # ================================================================================================
    # status for the UI
    # ================================================================================================

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(STATUS_EVERY_S)
            try:
                self.bus.publish("status", status=self.status())
            except Exception:
                log.exception("status snapshot failed")

    def _fastest_info(self, me) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
        best = self.world.best_lap_overall()
        if best is None:
            return None
        car, ms = best
        mine = me.session_best_ms
        return {
            "name": car.name,
            "time": lap_time(ms),
            "delta": round((mine - ms) / 1000, 3) if mine else None,
            "is_me": car is me,
        }

    def status(self) -> dict[str, Any]:
        w = self.world
        s = w.session
        me = w.player
        session = None
        if s is not None:
            session = {
                "track": names.track_name(s.track_id),
                "session": names.session_name(s.session_type),
                "weather": names.WEATHER.get(s.weather, ""),
                "track_temp": s.track_temp,
                "air_temp": s.air_temp,
                "total_laps": s.total_laps if w.is_race else None,
                "time_left": s.time_left_s if not w.is_race else None,
                "safety_car": s.safety_car_status,
                "is_race": w.is_race,
            }
        player = None
        if me is not None and me.lap is not None:
            ahead = w.car_at_position(me.lap.position - 1) if w.is_race else None
            behind = w.car_at_position(me.lap.position + 1) if w.is_race else None
            player = {
                "name": me.name,
                "position": me.lap.position,
                "lap": me.lap.lap_num,
                "speed": round(me.speed_kph),
                "gear": me.telemetry.gear if me.telemetry else None,
                "compound": names.compound_name(me.compound) if me.compound is not None else None,
                "tyre_age": me.status.tyre_age_laps if me.status else None,
                "wear": [round(x) for x in me.damage.tyre_wear] if me.damage else None,
                "tyre_temps": list(me.telemetry.tyre_inner_temps) if me.telemetry else None,
                "fuel_laps": round(me.status.fuel_remaining_laps, 1) if me.status else None,
                "battery": round(me.status.ers_store_j / 4e6 * 100) if me.status else None,
                "best_lap": lap_time(me.session_best_ms) if me.session_best_ms else None,
                "last_lap": lap_time(me.lap.last_lap_ms) if me.lap.last_lap_ms else None,
                "fastest": self._fastest_info(me) if not w.is_race else None,
                "ahead": {"name": ahead.name, "gap": round(w.race_gap_s(ahead, me) or 0, 1)} if ahead else None,
                "behind": {"name": behind.name, "gap": round(w.race_gap_s(me, behind) or 0, 1)} if behind else None,
                "status": {0: "Garage", 1: "Push lap", 2: "In lap", 3: "Out lap", 4: "On track"}.get(
                    me.lap.driver_status, ""
                ),
                "in_pits": me.in_pit_lane,
            }
        p = self.settings.ptt
        binding = Binding(p.kind, p.device_name, p.device_guid, p.button, p.key, p.udp_action)
        return {
            "telemetry": {
                "connected": self.stats.connected,
                "rate": round(self.stats.rate),
                "packets": self.stats.packets,
                "format": self.decoder.last_format,
                "port": self.settings.telemetry.port,
                "error": self.port_error,
                "warning": self.decoder.last_warning,
                "replaying": self.replaying,
            },
            "session": session,
            "player": player,
            "voice": {
                "engine": self.tts.active,
                "wanted": self.settings.voice.engine,
                "status": self.tts.status,
                "error": self.tts.error,
                "model": self.downloader.progress(),
                "output_error": self.player.error,
                "speaking": self.speaker.speaking.text if self.speaker.speaking else None,
                "level": round(self.player.level, 3),
            },
            "mic": {
                "ok": self.mic.ok,
                "error": self.mic.error,
                "level": round(self.mic.level, 4),
                "recording": self.mic.recording,
            },
            "stt": {
                "ready": self.stt.ready,
                "status": self.stt.status,
                "error": self.stt.error,
                "device": self.stt.device,
            },
            "ptt": {
                "binding": binding.describe(),
                "kind": p.kind,
                "mode": p.mode,
                "active": bool(self.ptt and self.ptt.active),
                "controllers": self.ptt.joystick.devices() if self.ptt else [],
            },
            "ai": {
                "available": self.brain.available,
                "enabled": self.settings.ai.enabled,
                "provider": self.brain.provider.id,
                "provider_label": self.brain.provider.label,
                "problem": self.settings.ai_problem,
                "has_key": bool(self.settings.api_key),
                "status": self.brain.status,
                "error": self.brain.disabled_reason or self.brain.last_error,
                "model": self.brain.model or "auto",
                "latency": round(self.brain.last_latency, 2) if self.brain.last_latency else None,
            },
            "radio": {
                "pending": [c.text for c in self.director.pending()][:5],
                "why_quiet": self.director.why_quiet(self.world),
                "muted_for": max(0, round(self.director.muted_until - time.monotonic())),
                "verbosity": self.settings.engineer.verbosity,
            },
            "recording": {
                "active": self.recorder.active,
                "file": self.recorder.current.name if self.recorder.current else None,
                "error": self.recorder.error,
            },
            "onboarded": self.settings.onboarded,
        }
