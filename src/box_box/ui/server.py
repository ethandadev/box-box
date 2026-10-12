"""The local web UI: setup wizard, live dashboard, settings, diagnostics and recordings.

Served on 127.0.0.1 only. Requests must come from the UI's own origin (host check + JSON-only POSTs),
so a website open in the same browser can't drive the engineer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from box_box import __version__, config, diagnostics, logs, paths
from box_box.app import Engine
from box_box.brain import providers
from box_box.telemetry.autorecord import list_recordings
from box_box.voice.player import list_devices

log = logging.getLogger(__name__)

STATIC = resources.files("box_box.ui") / "static"


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, default=str))


@web.middleware
async def guard(request: web.Request, handler):  # type: ignore[no-untyped-def]
    host = request.host.split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return web.Response(status=403, text="Box Box only answers on localhost.")
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1].split(":")[0] not in ("127.0.0.1", "localhost"):
            return web.Response(status=403, text="Cross-origin request refused.")
        if request.content_type != "application/json":
            return web.Response(status=415, text="JSON only.")
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        log.exception("UI request %s failed", request.path)
        return _json({"ok": False, "error": str(e)}, status=500)


class UIServer:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.app = web.Application(middlewares=[guard])
        self._learning = False
        r = self.app.router
        r.add_get("/", self.index)
        r.add_get("/static/{name}", self.static)
        r.add_get("/ws", self.ws)
        r.add_get("/api/state", self.state)
        r.add_post("/api/settings", self.settings)
        r.add_get("/api/devices", self.devices)
        r.add_post("/api/voice/test", self.voice_test)
        r.add_post("/api/voice/download", self.voice_download)
        r.add_post("/api/ptt/learn", self.ptt_learn)
        r.add_post("/api/ptt/udp", self.ptt_udp)
        r.add_post("/api/ptt/clear", self.ptt_clear)
        r.add_post("/api/ptt/ui", self.ptt_ui)
        r.add_post("/api/ask", self.ask)
        r.add_get("/api/diagnostics", self.diag)
        r.add_get("/api/report", self.report)
        r.add_post("/api/fix", self.fix)
        r.add_get("/api/recordings", self.recordings)
        r.add_get("/api/feedback", self.feedback)
        r.add_post("/api/replay", self.replay)
        r.add_post("/api/replay/stop", self.replay_stop)
        r.add_post("/api/ai/test", self.ai_test)
        r.add_post("/api/ai/models", self.ai_models)
        r.add_post("/api/open", self.open_folder)
        r.add_get("/api/logs", self.logs)
        self._runner: web.AppRunner | None = None

    async def start(self, port: int) -> int:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        for candidate in [port, *range(port + 1, port + 10)]:
            try:
                site = web.TCPSite(self._runner, "127.0.0.1", candidate)
                await site.start()
                log.info("UI on http://127.0.0.1:%d", candidate)
                return candidate
            except OSError as e:
                log.warning("UI port %d unavailable (%s), trying the next one", candidate, e)
        raise OSError(f"no free port for the UI near {port}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    # ---- pages ------------------------------------------------------------------------------------

    async def index(self, request: web.Request) -> web.Response:
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{version}}", __version__)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def static(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if "/" in name or "\\" in name or name.startswith("."):
            raise web.HTTPNotFound()
        res = STATIC / name
        if not res.is_file():
            raise web.HTTPNotFound()
        ctype = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png"}.get(
            Path(name).suffix, "application/octet-stream"
        )
        return web.Response(body=res.read_bytes(), content_type=ctype, headers={"Cache-Control": "no-store"})

    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        q = self.engine.bus.subscribe()
        try:
            await ws.send_str(json.dumps({"type": "status", "status": self.engine.status()}, default=str))
            for event in list(self.engine.bus.history)[-60:]:
                await ws.send_str(json.dumps({**event, "replayed": True}, default=str))

            async def reader() -> None:
                async for msg in ws:
                    if msg.type == WSMsgType.ERROR:
                        break

            read_task = asyncio.create_task(reader())
            while not ws.closed and not read_task.done():
                try:
                    event = await asyncio.wait_for(q.get(), 1.0)
                except TimeoutError:
                    continue
                await ws.send_str(json.dumps(event, default=str))
            read_task.cancel()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.engine.bus.unsubscribe(q)
        return ws

    # ---- state & settings -------------------------------------------------------------------------

    async def state(self, request: web.Request) -> web.Response:
        e = self.engine
        return _json(
            {
                "version": __version__,
                "status": e.status(),
                "settings": e.settings.to_dict(redact=True),
                "schema": config.schema(),
                "providers": providers.public(),
                "devices": self._devices(),
                "home": str(paths.app_home()),
                "recordings_dir": str(e.settings.recordings_dir()),
            }
        )

    @staticmethod
    def _devices() -> dict[str, list[dict]]:
        return {
            "output": [{"name": d.name, "default": d.default, "api": d.hostapi} for d in list_devices("output")],
            "input": [{"name": d.name, "default": d.default, "api": d.hostapi} for d in list_devices("input")],
        }

    async def settings(self, request: web.Request) -> web.Response:
        patch = await request.json()
        if not isinstance(patch, dict):
            return _json({"ok": False, "error": "expected an object"}, 400)
        s = await asyncio.to_thread(self.engine.store.update, patch)
        return _json({"ok": True, "settings": s.to_dict(redact=True)})

    async def devices(self, request: web.Request) -> web.Response:
        return _json(await asyncio.to_thread(self._devices))

    # ---- voice & push-to-talk ---------------------------------------------------------------------

    async def voice_test(self, request: web.Request) -> web.Response:
        body = await request.json()
        s = self.engine.settings
        text = (body.get("text") or "").strip()[
            :300
        ] or f"Radio check, {s.driver.name}. {s.engineer.name} here, loud and clear."
        self.engine.say_now(text)
        return _json({"ok": True})

    async def voice_download(self, request: web.Request) -> web.Response:
        self.engine.downloader.start()
        return _json({"ok": True, "progress": self.engine.downloader.progress()})

    async def ptt_learn(self, request: web.Request) -> web.Response:
        if self.engine.ptt is None:
            return _json({"ok": False, "error": "Push-to-talk is disabled."}, 400)
        if self._learning:
            return _json({"ok": False, "error": "Already listening for a button."}, 409)
        self._learning = True
        try:
            binding = await asyncio.to_thread(self.engine.ptt.learn, 15.0)
        finally:
            self._learning = False
        if binding is None:
            return _json({"ok": False, "error": "No button press detected. Is the wheel on and connected?"})
        self.engine.store.update(
            {
                "ptt": {
                    "kind": binding.kind,
                    "device_name": binding.device_name,
                    "device_guid": binding.device_guid,
                    "button": binding.button,
                    "key": binding.key,
                }
            }
        )
        return _json({"ok": True, "binding": binding.describe()})

    async def ptt_udp(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = max(1, min(12, int(body.get("action", 1))))
        self.engine.store.update({"ptt": {"kind": "udp_action", "udp_action": action, "mode": "hold"}})
        return _json({"ok": True})

    async def ptt_clear(self, request: web.Request) -> web.Response:
        self.engine.store.update(
            {"ptt": {"kind": "none", "device_name": "", "device_guid": "", "button": -1, "key": ""}}
        )
        return _json({"ok": True})

    async def ptt_ui(self, request: web.Request) -> web.Response:
        body = await request.json()
        down = bool(body.get("down"))
        if self.engine.ptt is not None:
            self.engine.ptt.ui_button(down)
        else:
            self.engine.on_ptt(down)
        return _json({"ok": True})

    async def ask(self, request: web.Request) -> web.Response:
        body = await request.json()
        text = str(body.get("text", "")).strip()[:500]
        if not text:
            return _json({"ok": False, "error": "Say something first."}, 400)
        asyncio.ensure_future(self.engine.respond(text))
        return _json({"ok": True})

    # ---- diagnostics ------------------------------------------------------------------------------

    async def diag(self, request: web.Request) -> web.Response:
        return _json({"checks": await asyncio.to_thread(diagnostics.run_checks, self.engine)})

    async def report(self, request: web.Request) -> web.Response:
        text = await asyncio.to_thread(diagnostics.report, self.engine)
        return web.Response(text=text, content_type="text/plain")

    async def logs(self, request: web.Request) -> web.Response:
        lines = [
            {"t": line.time, "level": line.level, "name": line.name, "msg": line.message}
            for line in logs.ring.snapshot(200)
        ]
        return _json({"lines": lines})

    async def fix(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = body.get("action")
        e = self.engine
        s = e.settings
        if action == "reopen_audio":
            await asyncio.to_thread(e.player.open, s.voice.output_device)
            return _json({"ok": e.player.error is None, "message": e.player.error or "Audio output reopened."})
        if action == "reopen_mic":
            await asyncio.to_thread(e.mic.open, s.mic.input_device)
            return _json({"ok": e.mic.error is None, "message": e.mic.error or "Microphone reopened."})
        if action == "reload_stt":
            e.stt._model = None
            e.stt.load_async(s.mic.stt_model, s.mic.stt_device)
            return _json({"ok": True, "message": "Reloading speech recognition..."})
        if action == "retry_ai":
            e.brain.reset_errors()
            return await self.ai_test(request)
        if action == "download_voice":
            e.downloader.start()
            return _json({"ok": True, "message": "Downloading the voice..."})
        if action == "install_gpu_speech":
            ok, message = await asyncio.to_thread(diagnostics.install_gpu_speech)
            if ok:
                e.stt._model = None
                from box_box.voice.stt import add_nvidia_dll_dirs

                add_nvidia_dll_dirs()
                e.stt.load_async(s.mic.stt_model, "cuda")
                message += " (if it still uses the CPU, restart Box Box)"
            return _json({"ok": ok, "message": message})
        return _json({"ok": False, "error": f"unknown action {action}"}, 400)

    async def ai_test(self, request: web.Request) -> web.Response:
        e = self.engine
        s = e.settings
        if not s.ai.enabled:
            return _json({"ok": False, "message": "AI conversation is turned off."})
        if s.ai_problem:
            return _json({"ok": False, "message": s.ai_problem})
        e.brain.reset_errors()
        started = time.perf_counter()
        try:
            text = await asyncio.wait_for(
                e.brain.complete("Radio check. Reply with one short sentence.", max_tokens=300), 45
            )
        except TimeoutError:
            return _json({"ok": False, "message": f"{s.provider.label} didn't answer within 45 seconds."})
        except Exception as err:  # noqa: BLE001
            message = str(err) or f"Couldn't reach {s.provider.label}."
            try:  # the most common cause: a model name the service doesn't have
                hint = e.brain.unknown_model_hint(await asyncio.wait_for(e.brain.list_models(), 15))
                if hint:
                    message = f"{message} {hint}"
            except Exception:  # noqa: BLE001
                pass
            return _json({"ok": False, "message": message})
        took = time.perf_counter() - started
        if not text:
            return _json({"ok": False, "message": f"{e.brain.describe()} answered with nothing. Try another model."})
        return _json({"ok": True, "message": f"{e.brain.describe()} ({took:.1f} s): {text}"})

    async def ai_models(self, request: web.Request) -> web.Response:
        """The models the configured key/server can use, for the model picker."""
        e = self.engine
        s = e.settings
        if s.ai_problem:
            return _json({"ok": False, "message": s.ai_problem, "models": []})
        try:
            models = await asyncio.wait_for(e.brain.list_models(), 20)
        except Exception as err:  # noqa: BLE001
            return _json({"ok": False, "message": str(err) or "Couldn't list models.", "models": []})
        return _json({"ok": True, "models": sorted(set(models)), "message": f"{len(models)} models available."})

    # ---- recordings -------------------------------------------------------------------------------

    async def feedback(self, request: web.Request) -> web.StreamResponse:
        """One zip to send to whoever is improving the engineer: the session recording, everything said on
        the radio during it (engineer and driver), and the support report with the recent log."""
        name = request.query.get("name", "")
        folder = self.engine.settings.recordings_dir()
        path = (folder / name).resolve()
        if not name or path.parent != folder.resolve() or not path.is_file():
            return _json({"ok": False, "error": "recording not found"}, 404)

        def build() -> bytes:
            import io
            import zipfile

            st = path.stat()
            start = st.st_ctime if sys.platform == "win32" else st.st_mtime - 4 * 3600
            radio = self.engine.memory.radio_between(start - 60, st.st_mtime + 300)
            lines = [f"{time.strftime('%H:%M:%S', time.localtime(r['at']))}  {r['who']:8}  {r['text']}" for r in radio]
            import tempfile

            from box_box.telemetry.rawfile import compact

            stem = path.name.split(".f1raw")[0]
            buf = io.BytesIO()
            with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                # A shareable copy: only what the engineer uses, about 5x smaller, replays the same.
                small = Path(tmp) / f"{stem}.f1raw.gz"
                compact(path, small)
                z.write(small, small.name, compress_type=zipfile.ZIP_STORED)  # already gzip-compressed
                z.writestr("radio.txt", "\n".join(lines) + "\n" if lines else "(nothing was said)\n")
                z.writestr("support-report.txt", diagnostics.report(self.engine))
            return buf.getvalue()

        data = await asyncio.to_thread(build)
        stem = path.name.split(".f1raw")[0]
        return web.Response(
            body=data,
            content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="Box Box feedback - {stem}.zip"'},
        )

    async def recordings(self, request: web.Request) -> web.Response:
        folder = self.engine.settings.recordings_dir()
        return _json(
            {
                "folder": str(folder),
                "files": await asyncio.to_thread(list_recordings, folder),
                "sessions": await asyncio.to_thread(self.engine.memory.recent_sessions, 30),
                "replaying": self.engine.replaying,
            }
        )

    async def replay(self, request: web.Request) -> web.Response:
        body = await request.json()
        name = str(body.get("name", ""))
        folder = self.engine.settings.recordings_dir()
        path = (folder / name).resolve()
        if path.parent != folder.resolve() or not path.is_file():
            return _json({"ok": False, "error": "Recording not found."}, 404)
        speed = float(body.get("speed", 1.0))
        self.engine.start_replay(path, max(0.25, min(speed, 20.0)))
        return _json({"ok": True})

    async def replay_stop(self, request: web.Request) -> web.Response:
        self.engine.stop_replay()
        return _json({"ok": True})

    async def open_folder(self, request: web.Request) -> web.Response:
        body = await request.json()
        which = body.get("which")
        folder = {
            "recordings": self.engine.settings.recordings_dir(),
            "logs": paths.logs_dir(),
            "home": paths.app_home(),
        }.get(which)
        if folder is None:
            return _json({"ok": False}, 400)
        with contextlib.suppress(Exception):
            if sys.platform == "win32":
                os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])  # noqa: S603, S607
            else:
                subprocess.Popen(["xdg-open", str(folder)])  # noqa: S603, S607
        return _json({"ok": True, "path": str(folder)})
