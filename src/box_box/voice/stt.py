"""Speech recognition with faster-whisper, on the GPU when it works and the CPU when it doesn't.

On Windows, CTranslate2 needs NVIDIA's cuBLAS/cuDNN DLLs for the GPU. If they're installed as pip
packages (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) we add their folders to the DLL search path; if the
GPU can't load for any reason we fall back to the CPU so push-to-talk always works.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from box_box import paths
from box_box.voice.mic import SAMPLE_RATE

log = logging.getLogger(__name__)

F1_VOCAB = (
    "Formula 1 team radio. Box, box this lap, stay out, gap, delta, tyres, softs, mediums, hards, inters, wets, "
    "undercut, overcut, Plan B, DRS, overtake mode, battery, ERS, fuel, front wing, floor damage, puncture, "
    "safety car, VSC, yellow flag, pit window, sector, purple, push, copy, radio check."
)
# Whisper's favourite things to hear in silence.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching", "bye", "uh", "um", "."}

_dll_dirs_added = False


def add_nvidia_dll_dirs() -> list[str]:
    """Make pip-installed CUDA libraries visible to CTranslate2 on Windows."""
    global _dll_dirs_added
    if _dll_dirs_added or sys.platform != "win32":
        return []
    _dll_dirs_added = True
    added = []
    for base in map(Path, sys.path):
        nv = base / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))  # type: ignore[attr-defined]
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(bin_dir))
            except OSError:
                pass
    return added


def cuda_libraries_ok() -> bool:
    """On Windows, CTranslate2 needs cuBLAS and cuDNN DLLs; check before downloading a GPU model for nothing."""
    if sys.platform != "win32":
        return True
    import ctypes

    add_nvidia_dll_dirs()
    for dll in ("cublas64_12.dll", "cudnn_ops64_9.dll"):
        try:
            ctypes.WinDLL(dll)
        except OSError:
            return False
    return True


def cuda_devices() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001
        return 0


def _explain(e: Exception) -> str:
    text = str(e) or type(e).__name__
    low = text.lower()
    if any(k in low for k in ("403", "connect", "timed out", "resolve", "offline", "network", "ssl")):
        return f"couldn't download the speech model ({text}). Check your internet connection, then click Fix"
    if "cublas" in low or "cudnn" in low or ".dll" in low or "cuda" in low:
        return f"NVIDIA libraries missing ({text})"
    return text


@dataclass
class Transcript:
    text: str
    seconds: float  # audio length
    took: float  # processing time


class SpeechToText:
    def __init__(self) -> None:
        self._model = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self._load_lock = threading.Lock()
        self.status = "not loaded"
        self.device = ""
        self.model_name = ""
        self.error: str | None = None
        self.extra_vocab = ""
        self.gpu_libraries_missing = False

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load_async(self, model: str = "auto", device: str = "auto") -> Future[None]:
        return self._executor.submit(self.load, model, device)

    def load(self, model: str = "auto", device: str = "auto") -> None:
        with self._load_lock:
            try:
                from faster_whisper import WhisperModel
            except Exception as e:  # noqa: BLE001
                self.status, self.error = "unavailable", f"faster-whisper isn't installed: {e}"
                log.error(self.error)
                return
            add_nvidia_dll_dirs()
            cpu_model = model if model not in ("auto", "large-v3-turbo") else "small.en"
            attempts: list[tuple[str, str, str]] = []
            self.gpu_libraries_missing = False
            if device != "cpu" and cuda_devices() > 0:
                if cuda_libraries_ok():
                    attempts.append((model if model != "auto" else "large-v3-turbo", "cuda", "float16"))
                else:
                    self.gpu_libraries_missing = True
                    log.info("NVIDIA GPU found but cuBLAS/cuDNN aren't installed; speech recognition will use the CPU")
            attempts.append((cpu_model, "cpu", "int8"))  # always keep a CPU fallback
            errors: list[str] = []
            for name, dev, compute in attempts:
                self.status = f"loading {name} on {dev.upper()} (first run downloads it)"
                log.info("speech recognition: loading %s on %s", name, dev)
                try:
                    m = WhisperModel(
                        name, device=dev, compute_type=compute, download_root=str(paths.models_dir() / "whisper")
                    )
                    # A tiny warm-up catches missing CUDA DLLs now rather than on the first radio call.
                    list(m.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), language="en", beam_size=1)[0])
                    self._model, self.device, self.model_name = m, dev, name
                    self.status = f"ready ({name} on {dev.upper()})"
                    self.error = "; ".join(errors) or None
                    log.info("speech recognition ready: %s on %s", name, dev)
                    return
                except Exception as e:  # noqa: BLE001
                    log.warning("speech recognition %s on %s failed: %s", name, dev, e)
                    errors.append(f"{name} on {dev.upper()}: {_explain(e)}")
            self.status, self.error = "failed to load", "; ".join(errors)

    def transcribe_async(self, audio: np.ndarray) -> Future[Transcript]:
        return self._executor.submit(self.transcribe, audio)

    def transcribe(self, audio: np.ndarray) -> Transcript:
        import time

        seconds = len(audio) / SAMPLE_RATE
        start = time.perf_counter()
        if self._model is None:
            raise RuntimeError(self.error or "speech recognition isn't loaded")
        if seconds < 0.25 or float(np.sqrt(np.mean(audio**2))) < 0.003:
            return Transcript("", seconds, 0.0)  # nothing said: don't let Whisper invent something
        audio = audio / max(1e-3, float(np.max(np.abs(audio)))) * 0.9
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
            initial_prompt=(F1_VOCAB + " " + self.extra_vocab).strip(),  # vocabulary hint
        )
        prompt = (F1_VOCAB + " " + self.extra_vocab).strip()
        # Noise or a breath can come back as words: Whisper's own "probably not speech" plus low confidence
        # means it was guessing. A guess is worse than "say again", above all when it guesses "box box".
        kept = [
            s for s in segments if not (getattr(s, "no_speech_prob", 0) > 0.6 and getattr(s, "avg_logprob", 0) < -1.0)
        ]
        text = " ".join(s.text for s in kept).strip()
        clean = re.sub(r"[^\w ]", "", text.lower()).strip()
        if clean in HALLUCINATIONS:
            text = ""
        elif len(clean.split()) >= 3 and clean in re.sub(r"[^\w ]", "", prompt.lower()):
            text = ""  # the prompt echoed back on non-speech, not something the driver said
        return Transcript(text, seconds, time.perf_counter() - start)
