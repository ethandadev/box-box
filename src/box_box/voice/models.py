"""Download the voice model files on first run: resumable, checksum-verified, with progress for the UI."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from box_box import paths

log = logging.getLogger(__name__)

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    size: int


KOKORO_FILES = [
    # The full-precision model: the int8 one is smaller but audibly rougher (metallic, smeared vowels).
    ModelFile(
        "kokoro-v1.0.onnx",
        KOKORO_BASE + "kokoro-v1.0.onnx",
        "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
        325_532_387,
    ),
    ModelFile(
        "voices-v1.0.bin",
        KOKORO_BASE + "voices-v1.0.bin",
        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        28_214_398,
    ),
]


class DownloadError(RuntimeError):
    pass


class ModelDownloader:
    """Fetches a set of files into the models folder. Thread-safe progress for the UI."""

    def __init__(self, files: list[ModelFile], folder: Path | None = None) -> None:
        self.files = files
        self.folder = folder or paths.models_dir()
        self._lock = threading.Lock()
        self.state = "idle"  # idle | downloading | ready | error
        self.error: str | None = None
        self.done_bytes = 0
        self.total_bytes = sum(f.size for f in files)
        self._thread: threading.Thread | None = None

    def path(self, name: str) -> Path:
        return self.folder / name

    def installed(self) -> bool:
        return all(self.path(f.name).exists() and self.path(f.name).stat().st_size == f.size for f in self.files)

    def progress(self) -> dict:
        if self.installed():
            return {"state": "ready", "error": None, "percent": 100.0}
        with self._lock:
            pct = (self.done_bytes / self.total_bytes * 100) if self.total_bytes else 0.0
            return {"state": self.state, "error": self.error, "percent": round(min(pct, 99.9), 1)}

    def start(self) -> None:
        """Download in the background (no-op if already installed or running)."""
        if self.installed():
            self.state = "ready"
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="model-download", daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread:
            self._thread.join(timeout)
        return self.installed()

    def _run(self) -> None:
        with self._lock:
            self.state, self.error = "downloading", None
        try:
            for f in self.files:
                self._fetch(f)
            with self._lock:
                self.state = "ready"
            log.info("voice model ready in %s", self.folder)
        except Exception as e:  # noqa: BLE001 - report anything to the UI
            log.error("voice model download failed: %s", e)
            with self._lock:
                self.state, self.error = "error", str(e)

    def _fetch(self, f: ModelFile, attempts: int = 4) -> None:
        dest = self.path(f.name)
        if dest.exists() and dest.stat().st_size == f.size:
            self._add(f.size)
            return
        part = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(1, attempts + 1):
            try:
                have = part.stat().st_size if part.exists() else 0
                if have > f.size:
                    part.unlink()
                    have = 0
                if have == f.size:  # finished last time but closed before the rename: no range left to ask for
                    self._add(have)
                    self._verify(part, f)
                    os.replace(part, dest)
                    return
                req = urllib.request.Request(f.url, headers={"User-Agent": "box-box"})
                if have:
                    req.add_header("Range", f"bytes={have}-")
                self._add(have)
                with urllib.request.urlopen(req, timeout=30) as resp, open(part, "ab" if have else "wb") as out:
                    if have and resp.status != 206:  # server ignored the range: start over
                        out.truncate(0)
                        self._add(-have)
                    while chunk := resp.read(1 << 16):
                        out.write(chunk)
                        self._add(len(chunk))
                self._verify(part, f)
                os.replace(part, dest)
                return
            except Exception as e:
                # Undo progress counted for this file; the .part file keeps the bytes for resuming.
                with self._lock:
                    self.done_bytes = sum(
                        self.path(x.name).stat().st_size for x in self.files if self.path(x.name).exists()
                    )
                range_refused = isinstance(e, urllib.error.HTTPError) and e.code == 416
                if (isinstance(e, DownloadError) or range_refused) and part.exists():
                    part.unlink()  # corrupt, or a range the server won't serve: start this file over
                if attempt == attempts:
                    raise DownloadError(f"couldn't download {f.name}: {e}") from e
                log.warning("download of %s failed (%s), retrying", f.name, e)
                time.sleep(2**attempt)

    def _add(self, n: int) -> None:
        with self._lock:
            self.done_bytes += n

    @staticmethod
    def _verify(path: Path, f: ModelFile) -> None:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        if h.hexdigest() != f.sha256:
            raise DownloadError(f"{f.name} is corrupt (checksum mismatch)")


kokoro_files = ModelDownloader(KOKORO_FILES)
