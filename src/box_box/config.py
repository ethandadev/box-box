"""User settings: typed defaults, a JSON file on disk, and a schema the UI renders forms from.

Loading never fails: unknown keys are dropped, wrong types fall back to defaults, and a corrupt file is
backed up and replaced, so a bad edit can't stop the engineer from starting.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from box_box import paths
from box_box.brain import providers

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def opt(default: Any, label: str, help: str = "", **meta: Any) -> Any:
    """A settings field with UI metadata: label, help text, and optional choices/min/max/step/secret."""
    if isinstance(default, (list, dict)):
        return field(default_factory=lambda: type(default)(default), metadata={"label": label, "help": help, **meta})
    return field(default=default, metadata={"label": label, "help": help, **meta})


VOICES = [
    ("am_michael", "Michael (American, warm)"),
    ("am_adam", "Adam (American, deep)"),
    ("am_eric", "Eric (American, crisp)"),
    ("am_liam", "Liam (American, young)"),
    ("am_onyx", "Onyx (American, low)"),
    ("am_echo", "Echo (American, bright)"),
    ("am_fenrir", "Fenrir (American, gravelly)"),
    ("am_puck", "Puck (American, upbeat)"),
    ("af_heart", "Heart (American, female)"),
    ("af_bella", "Bella (American, female)"),
    ("af_nicole", "Nicole (American, female, soft)"),
    ("bm_george", "George (British)"),
    ("bm_lewis", "Lewis (British)"),
    ("bm_daniel", "Daniel (British)"),
    ("bf_emma", "Emma (British, female)"),
]


@dataclass
class DriverSettings:
    name: str = opt("Ethan", "Your name", "What the engineer calls you on the radio.")


@dataclass
class EngineerSettings:
    name: str = opt("Jake", "Engineer's name", "Your race engineer's name.")
    personality: str = opt(
        "calm",
        "Personality",
        "Calm is classic F1 radio. Fired up gets louder when it matters. Dry adds deadpan humour.",
        choices=[("calm", "Calm and precise"), ("fired_up", "Fired up"), ("dry", "Dry humour")],
    )
    verbosity: str = opt(
        "normal",
        "How much he talks",
        "Quiet only gives the essentials. Chatty adds more gaps, lap times and encouragement.",
        choices=[("quiet", "Quiet"), ("normal", "Normal"), ("chatty", "Chatty")],
    )
    natural_speech: str = opt(
        "subtle",
        "Natural speech",
        "Real engineers say 'uh', restart a sentence or correct themselves ('1.3... sorry, 1.2'). "
        "Never on urgent calls, and a correction always ends on the right number.",
        choices=[("off", "Off (scripted)"), ("subtle", "Subtle"), ("human", "Very human")],
    )
    units: str = opt(
        "metric", "Units", "", choices=[("metric", "Metric (km/h, °C, kg)"), ("imperial", "Imperial (mph, °F, lb)")]
    )


@dataclass
class VoiceSettings:
    engine: str = opt(
        "kokoro",
        "Voice engine",
        "Kokoro runs on your PC. The cloud voices sound properly human; if one fails, Kokoro steps in.",
        choices=[
            ("kokoro", "Kokoro (on your PC, free)"),
            ("gemini", "Google Gemini (very human, free tier)"),
            ("openai", "OpenAI (very human, about 1.5 cents a minute)"),
            ("elevenlabs", "ElevenLabs (very human, needs credits)"),
            ("windows", "Windows built-in"),
            ("off", "Off (text only)"),
        ],
    )
    voice: str = opt("am_michael", "Voice", "", choices=VOICES, when={"engine": ["kokoro"]})
    gemini_key: str = opt(
        "",
        "Gemini API key",
        "Free from aistudio.google.com/apikey. Leave empty to use the AI section's Gemini key.",
        secret=True,
        when={"engine": ["gemini"]},
    )
    openai_key: str = opt(
        "",
        "OpenAI API key",
        "From platform.openai.com. Leave empty to use the AI section's OpenAI key.",
        secret=True,
        when={"engine": ["openai"]},
    )
    elevenlabs_key: str = opt(
        "",
        "ElevenLabs API key",
        "From elevenlabs.io (free tier works). Stored only on this PC.",
        secret=True,
        when={"engine": ["elevenlabs"]},
    )
    cloud_voice: str = opt(
        "",
        "Cloud voice",
        "Leave empty for a good default. OpenAI: ash, onyx, echo, ballad, sage, verse. Gemini: Charon, Orus, "
        "Fenrir, Puck. ElevenLabs: a voice ID from your voice library.",
        when={"engine": ["openai", "elevenlabs", "gemini"]},
    )
    style: str = opt(
        "",
        "How he should sound",
        "Directions for the OpenAI and Gemini voices, e.g. 'calm British race engineer, quick and dry'. "
        "Leave empty for a classic F1 engineer.",
        when={"engine": ["openai", "gemini"]},
    )
    speed: float = opt(
        1.25,
        "Speaking speed",
        "Real engineers talk fast. Applies to every voice; the cloud voices are sped up without changing pitch.",
        min=0.8,
        max=1.6,
        step=0.02,
    )
    volume: float = opt(0.9, "Volume", "", min=0.0, max=1.0, step=0.05)
    radio_effect: float = opt(
        0.5, "Radio effect", "0 is studio-clean, 1 is a crackly team radio.", min=0.0, max=1.0, step=0.05
    )
    beeps: bool = opt(True, "Radio beeps", "Play the click and beep when he keys the radio.")
    ptt_beeps: bool = opt(True, "Push-to-talk beeps", "A click and chirp when you press and release your radio button.")
    output_device: str = opt(
        "", "Speakers / headset", "Leave on default to use the Windows default output.", device="output"
    )


@dataclass
class MicSettings:
    input_device: str = opt("", "Microphone", "Leave on default to use the Windows default microphone.", device="input")
    stt_model: str = opt(
        "auto",
        "Speech recognition model",
        "Auto picks large-v3-turbo on an NVIDIA GPU and small.en on CPU.",
        choices=[
            ("auto", "Auto"),
            ("large-v3-turbo", "large-v3-turbo (best, GPU)"),
            ("small.en", "small.en (fast, good)"),
            ("base.en", "base.en (fastest)"),
        ],
    )
    stt_device: str = opt(
        "auto", "Run speech recognition on", "", choices=[("auto", "Auto"), ("cuda", "NVIDIA GPU"), ("cpu", "CPU")]
    )


@dataclass
class PushToTalkSettings:
    mode: str = opt(
        "hold", "Button mode", "", choices=[("hold", "Hold to talk"), ("toggle", "Press to start, press to stop")]
    )
    kind: str = opt("none", "Button type", "Set with the 'Learn button' tool.", hidden=True)
    device_name: str = opt("", "Wheel / controller", "", hidden=True)
    device_guid: str = opt("", "Device id", "", hidden=True)
    button: int = opt(-1, "Button number", "", hidden=True)
    key: str = opt("", "Keyboard key", "", hidden=True)
    udp_action: int = opt(1, "Game UDP action", "", hidden=True)


@dataclass
class TelemetrySettings:
    port: int = opt(20777, "UDP port", "Must match Telemetry > UDP Port in F1 25.", min=1024, max=65535)
    listen_on_network: bool = opt(
        False,
        "Accept telemetry from other devices",
        "Only needed if F1 25 runs on another PC or a console. Windows will ask to allow it through the firewall.",
    )
    auto_record: bool = opt(
        True, "Auto-record sessions", "Save every session so it can be replayed or shared for tuning."
    )
    recordings_dir: str = opt("", "Recordings folder", "Leave empty for the default folder.")
    keep_recordings: int = opt(30, "Recordings to keep", "Oldest recordings are deleted beyond this.", min=1, max=1000)


@dataclass
class AISettings:
    enabled: bool = opt(
        True, "Use an AI model for conversation", "Without it the engineer still answers the common questions."
    )
    provider: str = opt(
        "anthropic",
        "AI service",
        "Which AI the engineer thinks with. Ollama and LM Studio run on your PC for free.",
        choices=providers.choices(),
    )
    model: str = opt(
        "",
        "Model",
        "Leave empty for the recommended model, or type any model id your service offers.",
        suggest="models",
    )
    api_key: str = opt(
        "", "Anthropic API key", "Stored only on this PC.", secret=True, when={"provider": ["anthropic"]}
    )
    openai_key: str = opt("", "OpenAI API key", "Stored only on this PC.", secret=True, when={"provider": ["openai"]})
    gemini_key: str = opt("", "Gemini API key", "Stored only on this PC.", secret=True, when={"provider": ["gemini"]})
    openrouter_key: str = opt(
        "", "OpenRouter API key", "Stored only on this PC.", secret=True, when={"provider": ["openrouter"]}
    )
    poe_key: str = opt("", "Poe API key", "Stored only on this PC.", secret=True, when={"provider": ["poe"]})
    custom_key: str = opt("", "API key (if the server needs one)", "", secret=True, when={"provider": ["custom"]})
    base_url: str = opt(
        "",
        "Server address",
        "Leave empty for the usual address (Ollama: http://localhost:11434/v1, LM Studio: http://localhost:1234/v1).",
        when={"provider": ["ollama", "lmstudio", "custom"]},
    )
    fast_answers: bool = opt(
        True,
        "Instant answers for common questions",
        "Gaps, tyres, fuel, position etc. are answered locally with no delay.",
    )
    debrief: bool = opt(True, "Spoken debrief after sessions", "")


@dataclass
class CalloutSettings:
    traffic: bool = opt(
        True, "Traffic (practice & quali)", "Cars on push laps behind you, slow cars ahead, cars leaving the pits."
    )
    flags: bool = opt(True, "Flags & safety car", "")
    gaps: bool = opt(True, "Gaps in races", "")
    gap_every_laps: int = opt(2, "Gap update every N laps", "", min=1, max=10)
    lap_feedback: bool = opt(True, "Lap times & sector feedback", "")
    positions: bool = opt(True, "Position changes", "")
    tyres: bool = opt(True, "Tyre temperatures & wear", "")
    damage: bool = opt(True, "Damage", "")
    weather: bool = opt(True, "Weather changes", "")
    fuel: bool = opt(True, "Fuel", "")
    energy: bool = opt(True, "Battery, ERS mode & overtake", "")
    setup: bool = opt(
        True,
        "Diff & brake bias tips",
        "Wheelspin or lock-ups at the same corner lap after lap: what to change on the MFD.",
    )
    pit: bool = opt(True, "Pit window & strategy", "")
    encouragement: bool = opt(True, "Encouragement & banter", "")
    straights_only: bool = opt(
        True, "Only talk on straights", "Non-urgent messages wait until you're not braking or turning."
    )
    quiet_on_push_laps: bool = opt(
        True, "Quiet on qualifying push laps", "Only urgent calls during a quali flying lap."
    )


@dataclass
class UISettings:
    port: int = opt(8777, "Web UI port", "", min=1024, max=65535)
    open_browser: bool = opt(True, "Open the browser on start", "")


@dataclass
class Settings:
    version: int = SCHEMA_VERSION
    onboarded: bool = False
    driver: DriverSettings = field(default_factory=DriverSettings)
    engineer: EngineerSettings = field(default_factory=EngineerSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    mic: MicSettings = field(default_factory=MicSettings)
    ptt: PushToTalkSettings = field(default_factory=PushToTalkSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)
    ai: AISettings = field(default_factory=AISettings)
    callouts: CalloutSettings = field(default_factory=CalloutSettings)
    ui: UISettings = field(default_factory=UISettings)

    def to_dict(self, redact: bool = False) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if redact:
            for section, key in SECRETS:
                value = data[section][key]
                if value:  # a short key would be shown whole by its last four characters
                    data[section][key] = REDACTED + (value[-4:] if len(value) > 8 else "")
        return data

    @property
    def provider(self) -> providers.Provider:
        return providers.get(self.ai.provider)

    @property
    def api_key(self) -> str:
        """The key for the selected AI service (settings first, then its environment variable)."""
        return providers.key_for(self.provider, self.ai)

    @property
    def ai_problem(self) -> str | None:
        """Why the AI can't be used as configured, or None."""
        return providers.missing(self.provider, self.ai)

    def recordings_dir(self) -> Path:
        if self.telemetry.recordings_dir.strip():
            p = Path(self.telemetry.recordings_dir).expanduser()
            try:
                p.mkdir(parents=True, exist_ok=True)
                return p
            except OSError as e:
                log.warning("recordings folder %s unusable (%s); using the default", p, e)
        return paths.default_recordings_dir()


SECTIONS = [f.name for f in fields(Settings) if f.name not in ("version", "onboarded")]
SECRET_FIELDS = [f.name for f in fields(AISettings) if f.metadata.get("secret")]
SECRETS = [
    (section, f.name)
    for section in SECTIONS
    for f in fields(type(getattr(Settings(), section)))
    if f.metadata.get("secret")
]
REDACTED = "••••"
OLD_DEFAULT_SPEEDS = (1.08, 1.15)  # untouched old defaults move to today's (engineers talk fast)


def _coerce(value: Any, default: Any, meta: dict[str, Any]) -> Any:
    """Return `value` converted to the default's type and clamped to its range, or the default if it can't be."""
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, int):
            v = int(value)
        elif isinstance(default, float):
            v = float(value)
        elif isinstance(default, str):
            v = "" if value is None else str(value)
            choices = meta.get("choices")
            if choices and v not in {c[0] for c in choices}:
                return default
            return v
        else:
            return value
        if "min" in meta:
            v = max(meta["min"], v)
        if "max" in meta:
            v = min(meta["max"], v)
        return v
    except (TypeError, ValueError):
        return default


def from_dict(data: dict[str, Any]) -> Settings:
    s = Settings()
    if not isinstance(data, dict):
        return s
    s.onboarded = bool(data.get("onboarded", False))
    voice = data.get("voice")
    if isinstance(voice, dict) and (voice.get("speed") in OLD_DEFAULT_SPEEDS or voice.get("radio_effect") == 0.7):
        # Old defaults: too slow and drawn-out, and too much distortion. Untouched ones move to the new defaults.
        voice = {**voice}
        if voice.get("speed") in OLD_DEFAULT_SPEEDS:
            voice["speed"] = VoiceSettings.speed
        if voice.get("radio_effect") == 0.7:
            voice["radio_effect"] = 0.5
        data = {**data, "voice": voice}
    for section in SECTIONS:
        target = getattr(s, section)
        incoming = data.get(section)
        if not isinstance(incoming, dict):
            continue
        for f in fields(target):
            if f.name in incoming:
                setattr(target, f.name, _coerce(incoming[f.name], getattr(target, f.name), dict(f.metadata)))
    return s


def merge(settings: Settings, patch: dict[str, Any]) -> Settings:
    """Apply a partial update like {"voice": {"volume": 0.5}} and return a new, validated Settings."""
    data = settings.to_dict()
    for key, value in patch.items():
        if key in SECTIONS and isinstance(value, dict):
            value = {k: v for k, v in value.items() if not str(v).startswith(REDACTED)}
            if key == "ai":
                # Keep stored keys when the UI sends back the redacted placeholder.
                value = {k: v for k, v in value.items() if not str(v).startswith(REDACTED)}
                # A model id from one service means nothing to another: switching service resets it.
                if "provider" in value and value["provider"] != data["ai"]["provider"] and "model" not in value:
                    value["model"] = ""
                for k in ("model", "base_url", *SECRET_FIELDS):
                    if isinstance(value.get(k), str):
                        value[k] = value[k].strip()
            data[key].update(value)
        elif key == "onboarded":
            data[key] = bool(value)
    return from_dict(data)


def schema() -> list[dict[str, Any]]:
    """Describe every setting for the UI: section, key, type, label, help, choices, range."""
    out = []
    for section in SECTIONS:
        cls = type(getattr(Settings(), section))
        for f in fields(cls):
            meta = dict(f.metadata)
            default = f.default
            kind = (
                "bool"
                if isinstance(default, bool)
                else "int"
                if isinstance(default, int)
                else ("float" if isinstance(default, float) else "str")
            )
            out.append(
                {
                    "section": section,
                    "key": f.name,
                    "type": kind,
                    "default": default,
                    "label": meta.get("label", f.name),
                    "help": meta.get("help", ""),
                    "choices": meta.get("choices"),
                    "min": meta.get("min"),
                    "max": meta.get("max"),
                    "step": meta.get("step"),
                    "secret": meta.get("secret", False),
                    "hidden": meta.get("hidden", False),
                    "device": meta.get("device"),
                    "when": meta.get("when"),
                    "suggest": meta.get("suggest"),
                }
            )
    return out


class SettingsStore:
    """Thread-safe owner of the current settings, persisted atomically to disk."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.settings_file()
        self._lock = threading.Lock()
        self._listeners: list[Any] = []
        self.settings = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return from_dict(data)
        except (OSError, ValueError) as e:
            backup = self.path.with_suffix(".broken.json")
            log.error("settings file unreadable (%s); backed up to %s and reset to defaults", e, backup)
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return Settings()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.settings.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def update(self, patch: dict[str, Any]) -> Settings:
        with self._lock:
            old = self.settings
            self.settings = merge(old, patch)
        self.save()
        for listener in list(self._listeners):
            try:
                listener(old, self.settings)
            except Exception:
                log.exception("settings listener failed")
        return self.settings

    def on_change(self, listener: Any) -> None:
        self._listeners.append(listener)
