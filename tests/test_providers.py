"""Multi-provider AI: settings, and the OpenAI-compatible backend against a real (fake) HTTP server."""

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from box_box import config
from box_box.brain import providers
from box_box.brain.backends import SentenceStream
from box_box.brain.llm import Brain, LLMError
from box_box.config import Settings
from tests.factories import Sim
from tests.test_brain import FakeActions, race_sim

# ---- settings ---------------------------------------------------------------------------------------


def test_switching_provider_resets_the_model_and_keeps_keys() -> None:
    s = Settings()
    s = config.merge(s, {"ai": {"model": "claude-sonnet-5-5", "api_key": "sk-ant-1234", "openai_key": " sk-oa-9999 "}})
    s = config.merge(s, {"ai": {"provider": "openai"}})
    assert s.ai.model == "" and s.ai.openai_key == "sk-oa-9999" and s.api_key == "sk-oa-9999"
    assert providers.model_for(s.provider, s.ai) == "gpt-5-mini"
    red = s.to_dict(redact=True)["ai"]
    assert red["api_key"] == "••••1234" and red["openai_key"] == "••••9999"
    # The UI sends redacted placeholders back: they must not overwrite the real keys.
    s = config.merge(s, {"ai": red})
    assert s.ai.api_key == "sk-ant-1234" and s.ai.openai_key == "sk-oa-9999"


def test_provider_problems_and_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings()
    s.ai.provider = "gemini"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert s.ai_problem == "No Google Gemini API key."
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-env")
    assert s.ai_problem is None and s.api_key == "AIza-env"
    s.ai.provider = "ollama"
    assert s.ai_problem is None and providers.base_url_for(s.provider, s.ai) == "http://localhost:11434/v1"
    s.ai.provider = "custom"
    assert s.ai_problem == "No server address set."
    s.ai.base_url = "http://box:8000/v1/"
    assert s.ai_problem is None and providers.base_url_for(s.provider, s.ai) == "http://box:8000/v1"
    # A free-text model id survives a save/load round trip.
    s.ai.model = "my-org/some-model:latest"
    assert config.from_dict(s.to_dict()).ai.model == "my-org/some-model:latest"


def test_unknown_provider_falls_back() -> None:
    assert config.from_dict({"ai": {"provider": "skynet"}}).ai.provider == "anthropic"


def test_think_blocks_are_never_spoken() -> None:
    out = SentenceStream()
    said = []
    for delta in ["<thi", "nk>The user wants. Let me", " check.</th", "ink>Gap is 1.2. ", "Push", " now."]:
        said += out.feed(delta)
    said += out.flush()
    assert said == ["Gap is 1.2.", "Push now."]


# ---- a fake OpenAI-compatible server -----------------------------------------------------------------


class FakeOpenAI:
    """Scripted /chat/completions and /models. Each script entry is a list of SSE chunks or (status, body)."""

    def __init__(self, script: list[Any], models: list[str] | None = None) -> None:
        self.script = list(script)
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.models = models or ["model-a", "model-b"]

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_get("/v1/models", self.list_models)
        return app

    async def chat(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        step = self.script.pop(0)
        if isinstance(step, tuple):
            status, payload = step
            return web.json_response(payload, status=status)
        if not body.get("stream"):
            return web.json_response(step)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for chunk in step:
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def list_models(self, request: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": [{"id": f"models/{m}"} for m in self.models]})


def text_chunks(*parts: str) -> list[dict]:
    return [{"choices": [{"index": 0, "delta": {"content": p}}]} for p in parts] + [
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    ]


def tool_chunks(name: str, args: dict) -> list[dict]:
    raw = json.dumps(args)
    half = len(raw) // 2
    return [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": name, "arguments": raw[:half]},
                            }
                        ]
                    },
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": raw[half:]}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]


async def with_server(fake: FakeOpenAI, provider: str, model: str, fn):  # type: ignore[no-untyped-def]
    server = TestServer(fake.app())
    await server.start_server()
    settings = Settings()
    settings.ai.provider = provider
    settings.ai.model = model
    settings.ai.custom_key = "secret-key"
    settings.ai.base_url = str(server.make_url("/v1"))
    brain = Brain(lambda: settings, lambda: "")
    try:
        return await fn(brain, settings)
    finally:
        await brain.aclose()
        await server.close()


def test_openai_compatible_stream_with_tool_call() -> None:
    fake = FakeOpenAI(
        [tool_chunks("driver", {"name": "Leclerc"}), text_chunks("Leclerc is 1.", "3 ahead. ", "Same tyres.")]
    )
    sim = race_sim()

    async def run(brain: Brain, _s: Settings) -> list[str]:
        return [x async for x in brain.reply("how's Leclerc", sim.world, FakeActions())]

    out = asyncio.run(with_server(fake, "custom", "racer-7b", run))
    assert out == ["Leclerc is 1.3 ahead.", "Same tyres."]
    first, second = fake.requests
    assert first["model"] == "racer-7b" and first["stream"] and first["tools"][0]["type"] == "function"
    assert first["messages"][0]["role"] == "system" and "Ethan" in first["messages"][0]["content"]
    assert fake.headers[0]["Authorization"] == "Bearer secret-key"
    assistant, tool = second["messages"][-2:]
    assert assistant["tool_calls"][0]["function"]["name"] == "driver"
    assert (
        tool["role"] == "tool" and tool["tool_call_id"] == "call_1" and json.loads(tool["content"])["name"] == "Leclerc"
    )


def test_rejected_parameters_are_dropped_and_retried() -> None:
    fake = FakeOpenAI(
        [
            (400, {"error": {"message": "Unsupported parameter: 'tools' is not supported with this model."}}),
            text_chunks("Copy. ", "Box this lap."),
            text_chunks("Still fine."),
        ]
    )

    async def run(brain: Brain, _s: Settings) -> tuple[list[str], list[str]]:
        a = [x async for x in brain.reply("box?", Sim([]).world, FakeActions())]
        b = [x async for x in brain.reply("and now?", Sim([]).world, FakeActions())]
        return a, b

    a, b = asyncio.run(with_server(fake, "custom", "tiny", run))
    assert a == ["Copy.", "Box this lap."] and b == ["Still fine."]
    assert "tools" in fake.requests[0] and "tools" not in fake.requests[1] and "tools" not in fake.requests[2]


def test_bad_key_is_fatal_and_disables_the_ai() -> None:
    fake = FakeOpenAI([(401, {"error": {"message": "Incorrect API key provided"}})])

    async def run(brain: Brain, _s: Settings) -> Brain:
        with pytest.raises(LLMError) as err:
            [x async for x in brain.reply("radio check", Sim([]).world, FakeActions())]
        assert err.value.fatal and "rejected" in str(err.value)
        return brain

    brain = asyncio.run(with_server(fake, "custom", "m", run))
    assert not brain.available and "rejected" in (brain.problem or "")
    brain.reset_errors()
    assert brain.available


def test_local_server_picks_the_loaded_model_and_lists_models() -> None:
    fake = FakeOpenAI(
        [{"choices": [{"message": {"role": "assistant", "content": "Loud and clear."}}]}],
        models=["qwen3:14b", "llama3.1:8b"],
    )

    async def run(brain: Brain, _s: Settings) -> tuple[str, list[str]]:
        return await brain.complete("Radio check."), await brain.list_models()

    text, models = asyncio.run(with_server(fake, "ollama", "", run))
    assert text == "Loud and clear." and fake.requests[0]["model"] == "qwen3:14b"
    assert models == ["qwen3:14b", "llama3.1:8b"]  # "models/" prefixes (Gemini) are stripped


def test_unreachable_local_server_says_so() -> None:
    settings = Settings()
    settings.ai.provider = "lmstudio"
    settings.ai.base_url = "http://127.0.0.1:9/v1"
    settings.ai.model = "x"
    brain = Brain(lambda: settings, lambda: "")

    async def run() -> None:
        try:
            with pytest.raises(LLMError, match="Is it running"):
                await brain.complete("hi")
        finally:
            await brain.aclose()

    asyncio.run(run())
    assert brain.available  # not fatal: start the server and it works
