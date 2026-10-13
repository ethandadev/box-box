"""Self-checks with plain-English fixes, for the Diagnostics page and `box-box doctor`."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

from box_box import __version__, paths

if TYPE_CHECKING:
    from box_box.app import Engine

log = logging.getLogger(__name__)

GAME_SETUP = (
    "In F1 25 go to Settings > Telemetry Settings: UDP Telemetry On, UDP Broadcast Mode Off, "
    "UDP IP Address 127.0.0.1, UDP Port {port}, UDP Send Rate 20Hz or more, UDP Format 2026."
)


def check(id: str, label: str, status: str, detail: str, fix: str = "", action: str | None = None) -> dict[str, Any]:
    return {"id": id, "label": label, "status": status, "detail": detail, "fix": fix, "action": action}


def run_checks(engine: Engine) -> list[dict[str, Any]]:
    s = engine.settings
    st = engine.status()
    out: list[dict[str, Any]] = []

    # ---- telemetry --------------------------------------------------------------------------------
    t = st["telemetry"]
    if t["error"]:
        out.append(check("telemetry", "Game telemetry", "error", t["error"], GAME_SETUP.format(port=s.telemetry.port)))
    elif t["replaying"]:
        out.append(check("telemetry", "Game telemetry", "ok", f"Playing the recording {t['replaying']}."))
    elif t["connected"]:
        fmt = t["format"]
        if fmt == 2025:
            out.append(
                check(
                    "telemetry",
                    "Game telemetry",
                    "warn",
                    f"Receiving {t['rate']} packets/s in the 2025 format.",
                    "Set UDP Format to 2026 in F1 25 for overtake mode, active aero and 24-car data.",
                )
            )
        else:
            out.append(check("telemetry", "Game telemetry", "ok", f"Receiving {t['rate']} packets/s (format {fmt})."))
    elif t["warning"]:
        out.append(
            check("telemetry", "Game telemetry", "error", t["warning"], GAME_SETUP.format(port=s.telemetry.port))
        )
    else:
        detail = (
            "Nothing received yet."
            if not t["packets"]
            else "The game stopped sending (paused, in the menus, or closed)."
        )
        out.append(
            check(
                "telemetry",
                "Game telemetry",
                "warn",
                detail + " That's normal until you're in a session.",
                GAME_SETUP.format(port=s.telemetry.port),
            )
        )

    # ---- voice ------------------------------------------------------------------------------------
    v = st["voice"]
    model = v["model"]
    if s.voice.engine == "off":
        out.append(
            check(
                "voice",
                "Engineer's voice",
                "off",
                "Voice is off: the radio is text only.",
                "Turn it on in Settings > Voice.",
            )
        )
    elif s.voice.engine == "kokoro" and model["state"] == "downloading":
        out.append(
            check(
                "voice",
                "Engineer's voice",
                "warn",
                f"Downloading the voice model, {model['percent']}%.",
                "Wait a minute (about 350 MB, first run only).",
            )
        )
    elif s.voice.engine == "kokoro" and model["state"] == "error":
        out.append(
            check(
                "voice",
                "Engineer's voice",
                "error",
                f"Voice model download failed: {model['error']}",
                "Check your internet connection and retry.",
                action="download_voice",
            )
        )
    elif v["engine"] != s.voice.engine:
        out.append(
            check(
                "voice",
                "Engineer's voice",
                "warn",
                f"Using the {v['engine']} voice instead: {v['error'] or v['status']}",
                "Retry, or pick another engine.",
            )
        )
    elif v.get("degraded"):
        out.append(
            check(
                "voice",
                "Engineer's voice",
                "warn",
                f"{v['error']} Lines are being said by the local voice for now.",
                "Top up or fix the key, or pick the local voice in Settings > Voice for one consistent voice.",
            )
        )
    else:
        out.append(check("voice", "Engineer's voice", "ok", f"{v['engine'].capitalize()} voice ready."))

    if v["output_error"]:
        out.append(
            check(
                "audio_out",
                "Speakers / headset",
                "error",
                v["output_error"],
                "Plug in your headset or pick another output in Settings > Voice.",
                action="reopen_audio",
            )
        )
    else:
        out.append(
            check(
                "audio_out",
                "Speakers / headset",
                "ok",
                f"Playing to {s.voice.output_device or 'the Windows default output'}.",
            )
        )

    # ---- microphone & speech ------------------------------------------------------------------------
    m = st["mic"]
    if not m["ok"]:
        out.append(
            check(
                "mic",
                "Microphone",
                "error",
                m["error"] or "Microphone not open.",
                "Check Windows Settings > Privacy > Microphone allows desktop apps, then pick your mic in Settings > Microphone.",
                action="reopen_mic",
            )
        )
    else:
        out.append(
            check("mic", "Microphone", "ok", f"Listening on {s.mic.input_device or 'the Windows default microphone'}.")
        )

    stt = st["stt"]
    if stt["ready"]:
        detail = f"Speech recognition {stt['status']}."
        if stt["device"] == "cpu" and gpu_name() and engine.stt.gpu_libraries_missing:
            out.append(
                check(
                    "stt",
                    "Speech recognition",
                    "warn",
                    detail + f" Your {gpu_name()} isn't being used (NVIDIA libraries missing).",
                    "Click Fix to install GPU support (about 1 GB, one time). CPU works fine, just a bit slower.",
                    action="install_gpu_speech",
                )
            )
        else:
            out.append(check("stt", "Speech recognition", "ok", detail))
    elif "loading" in stt["status"]:
        out.append(
            check(
                "stt",
                "Speech recognition",
                "warn",
                stt["status"].capitalize() + ".",
                "First run downloads the model (0.5 to 1.6 GB). Hang on.",
            )
        )
    else:
        out.append(
            check(
                "stt",
                "Speech recognition",
                "error",
                stt["error"] or stt["status"],
                "Click Fix to retry loading.",
                action="reload_stt",
            )
        )

    # ---- push-to-talk -------------------------------------------------------------------------------
    p = st["ptt"]
    if p["kind"] == "none":
        out.append(
            check(
                "ptt",
                "Push-to-talk",
                "warn",
                "No button set yet.",
                "Use 'Learn button' on the Setup page and press a button on your wheel.",
            )
        )
    elif p["kind"] == "joystick":
        names_seen = [c["name"] for c in p["controllers"]]
        if s.ptt.device_name and names_seen and s.ptt.device_name not in names_seen:
            out.append(
                check(
                    "ptt",
                    "Push-to-talk",
                    "error",
                    f"{s.ptt.device_name} isn't connected. Connected: {', '.join(names_seen)}.",
                    "Turn on your wheel base, or learn the button again.",
                )
            )
        else:
            out.append(check("ptt", "Push-to-talk", "ok", f"Bound to {p['binding']} ({p['mode']})."))
    else:
        out.append(check("ptt", "Push-to-talk", "ok", f"Bound to {p['binding']} ({p['mode']})."))

    # ---- AI -----------------------------------------------------------------------------------------
    ai = st["ai"]
    prov = s.provider
    label = f"Conversation ({prov.label})"
    if not s.ai.enabled:
        out.append(
            check(
                "ai",
                "Conversation (AI)",
                "off",
                "Off. The engineer still answers common questions.",
                "Turn it on in Settings > AI conversation.",
            )
        )
    elif ai["problem"]:
        if prov.needs_key:
            fix = f"Get a key at {prov.key_url} and paste it in Settings > AI conversation, or pick another AI service."
        else:
            fix = "Set the server address in Settings > AI conversation."
        out.append(check("ai", label, "warn", ai["problem"] + " The engineer answers common questions only.", fix))
    elif ai["error"]:
        fix = (
            f"Start {prov.label.split(' (')[0]} and load a model, then click Fix to retry."
            if prov.local
            else "Check the key and model in Settings > AI conversation, then click Fix to retry."
        )
        out.append(check("ai", label, "error", ai["error"], fix, action="retry_ai"))
    else:
        speed = f", first words in {ai['latency']} s" if ai.get("latency") else ""
        out.append(check("ai", label, "ok", f"Ready ({ai['model']}{speed})."))

    # ---- storage ------------------------------------------------------------------------------------
    rec = st["recording"]
    if rec["error"]:
        out.append(
            check(
                "recording",
                "Session recording",
                "error",
                rec["error"],
                "Pick another recordings folder in Settings > Telemetry.",
            )
        )
    elif s.telemetry.auto_record:
        out.append(check("recording", "Session recording", "ok", f"Recording sessions to {s.recordings_dir()}."))
    if engine.memory.error:
        out.append(
            check(
                "memory",
                "Engineer's memory",
                "warn",
                engine.memory.error,
                "If another copy of Box Box is open, close it and restart.",
            )
        )
    return out


def gpu_name() -> str | None:
    """The NVIDIA GPU's name, if there is one (via nvidia-smi, which ships with the driver)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=5
        )
        name = out.stdout.strip().splitlines()[0] if out.stdout.strip() else None
        return name
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def install_gpu_speech() -> tuple[bool, str]:
    """Install NVIDIA's cuBLAS/cuDNN wheels into this environment so speech recognition can use the GPU."""
    packages = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]
    uv = shutil.which("uv")
    cmd = (
        [uv, "pip", "install", "--python", sys.executable, *packages]
        if uv
        else [sys.executable, "-m", "pip", "install", *packages]
    )
    log.info("installing GPU speech support: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Install failed to start: {e}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        return False, "Install failed: " + " ".join(tail)
    return True, "Installed. Speech recognition will reload on the GPU."


def report(engine: Engine) -> str:
    """A copy-pasteable support report: versions, checks and the recent log."""
    from box_box import logs

    lines = [
        f"Box Box {__version__} on {platform.platform()} / Python {platform.python_version()}",
        f"Home: {paths.app_home()}",
        f"GPU: {gpu_name() or 'none detected'}",
        f"Uptime: {round(time.time() - engine.started_at)} s",
        "",
        "Checks:",
    ]
    for c in run_checks(engine):
        lines.append(f"  [{c['status'].upper():5}] {c['label']}: {c['detail']}")
    lines += ["", "Settings:"]
    settings = engine.settings.to_dict(redact=True)
    for section, values in settings.items():
        if isinstance(values, dict):
            lines.append(f"  {section}: " + ", ".join(f"{k}={v}" for k, v in values.items()))
    lines += ["", "Recent log:"]
    for line in logs.ring.snapshot(80):
        lines.append(
            f"  {time.strftime('%H:%M:%S', time.localtime(line.time))} {line.level:7} {line.name}: {line.message}"
        )
    return "\n".join(lines) + os.linesep
