import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from box_box.ui.server import UIServer
from tests.test_engine import make_engine


def run_with_client(tmp_path: Path, scenario) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(tmp_path)

    async def main() -> None:
        await engine.start()
        server = UIServer(engine)
        client = TestClient(TestServer(server.app, host="127.0.0.1"))
        await client.start_server()
        try:
            await scenario(client, engine)
        finally:
            await client.close()
            await engine.stop()

    asyncio.run(main())


def test_state_settings_and_ask(tmp_path: Path) -> None:
    async def scenario(client, engine) -> None:  # type: ignore[no-untyped-def]
        r = await client.get("/api/state")
        data = await r.json()
        assert data["settings"]["driver"]["name"] == "Ethan" and data["schema"]
        r = await client.post("/api/settings", json={"engineer": {"name": "Gianpiero"}, "ai": {"api_key": "sk-ant-secret-1234"}})
        body = await r.json()
        assert body["ok"] and body["settings"]["engineer"]["name"] == "Gianpiero"
        assert body["settings"]["ai"]["api_key"] == "••••1234"  # never sent back in full
        # Sending the redacted placeholder back doesn't overwrite the real key.
        await client.post("/api/settings", json={"ai": {"api_key": "••••1234"}})
        assert engine.settings.ai.api_key == "sk-ant-secret-1234"
        r = await client.post("/api/ask", json={"text": "radio check"})
        assert (await r.json())["ok"]
        await asyncio.sleep(0.5)
        assert any(e["type"] == "radio" and "Loud and clear" in e["text"] for e in engine.bus.history)
        r = await client.get("/")
        html = await r.text()
        assert "BOX&nbsp;BOX" in html and "{{version}}" not in html
        r = await client.get("/static/app.js")
        assert r.status == 200 and "javascript" in r.headers["Content-Type"]

    run_with_client(tmp_path, scenario)


def test_guards(tmp_path: Path) -> None:
    async def scenario(client, engine) -> None:  # type: ignore[no-untyped-def]
        r = await client.get("/api/state", headers={"Host": "evil.example:8777"})
        assert r.status == 403  # DNS rebinding
        r = await client.post("/api/ask", json={"text": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status == 403
        r = await client.post("/api/ask", data="text=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status == 415  # forms from other sites can't avoid a CORS preflight
        r = await client.get("/static/..%2Fserver.py")
        assert r.status == 404
        r = await client.post("/api/replay", json={"name": "../settings.json"})
        assert r.status == 404

    run_with_client(tmp_path, scenario)


def test_diagnostics_and_report(tmp_path: Path) -> None:
    async def scenario(client, engine) -> None:  # type: ignore[no-untyped-def]
        r = await client.get("/api/diagnostics")
        checks = (await r.json())["checks"]
        ids = {c["id"] for c in checks}
        assert {"telemetry", "voice", "mic", "stt", "ptt", "ai"} <= ids
        r = await client.get("/api/report")
        assert "Box Box" in await r.text()
        r = await client.get("/api/recordings")
        assert "files" in await r.json()
        r = await client.post("/api/fix", json={"action": "nope"})
        assert r.status == 400

    run_with_client(tmp_path, scenario)
