"""Wire protocols for the AI services: Anthropic's Messages API and OpenAI-style chat completions.

Both backends do the same job: stream a radio reply sentence by sentence while running the engineer's
data tools, make one-shot completions for debriefs, and list the models a key can use. Errors come out
as `LLMError`, with `fatal=True` when retrying is pointless until the settings change (bad key, no
credit, unknown model).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from box_box.brain.providers import Provider

log = logging.getLogger(__name__)

# (tool name, arguments) -> (result text, is_error)
ToolRunner = Callable[[str, dict[str, Any]], tuple[str, bool]]
MAX_TOOL_ROUNDS = 4


class LLMError(RuntimeError):
    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal  # bad key, no access: stop trying until settings change


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Pull complete sentences off the front of a streaming buffer."""
    parts = re.split(r"(?<=[.!?])\s+", buffer)
    if len(parts) == 1:
        return [], buffer
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


def clean_for_radio(text: str) -> str:
    text = re.sub(r"[*_#`>\[\]]", "", text)
    text = re.sub(r"\s*\n+\s*", " ", text)
    return text.strip()


class SentenceStream:
    """Turns streamed text deltas into clean sentences, dropping <think>…</think> reasoning blocks."""

    def __init__(self) -> None:
        self.buffer = ""
        self._raw = ""
        self._thinking = False
        self.spoken: list[str] = []

    def feed(self, delta: str) -> list[str]:
        self._raw += delta
        visible = ""
        while self._raw:
            if self._thinking:
                end = self._raw.find("</think>")
                if end < 0:
                    self._raw = self._raw[-8:]  # keep a possible partial closing tag
                    return []
                self._raw = self._raw[end + len("</think>") :]
                self._thinking = False
                continue
            start = self._raw.find("<think>")
            if start >= 0:
                visible += self._raw[:start]
                self._raw = self._raw[start + len("<think>") :]
                self._thinking = True
                continue
            # Hold back anything that might be the start of a "<think>" tag.
            cut = self._raw.rfind("<")
            if cut >= 0 and "<think>".startswith(self._raw[cut:]):
                visible += self._raw[:cut]
                self._raw = self._raw[cut:]
            else:
                visible += self._raw
                self._raw = ""
            break
        self.buffer += visible
        sentences, self.buffer = split_sentences(self.buffer)
        return self._keep(sentences)

    def flush(self) -> list[str]:
        tail = "" if self._thinking else self._raw
        self._raw = ""
        rest, self.buffer = self.buffer + tail, ""
        return self._keep([rest])

    def _keep(self, sentences: list[str]) -> list[str]:
        out = [c for c in (clean_for_radio(s) for s in sentences) if c]
        self.spoken += out
        return out


# =================================================================================================
# Anthropic
# =================================================================================================


class AnthropicBackend:
    def __init__(self, provider: Provider, key: str, base_url: str) -> None:
        import anthropic

        self.provider = provider
        self.client = anthropic.AsyncAnthropic(api_key=key, timeout=anthropic.Timeout(20.0, connect=5.0), max_retries=1)
        self._no_effort: set[str] = set()  # models that reject the effort setting (older Claude models)

    def _opts(self, model: str) -> dict[str, Any]:
        return {} if model in self._no_effort else {"output_config": {"effort": "low"}}

    def _effort_rejected(self, e: Exception, model: str) -> bool:
        """An older model refused `effort`: remember, so the request can go again without it."""
        import anthropic

        msg = str(getattr(e, "message", e)).lower()
        if (
            isinstance(e, anthropic.BadRequestError)
            and model not in self._no_effort
            and ("effort" in msg or "output_config" in msg)
        ):
            log.info("%s doesn't take the effort setting; retrying without it", model)
            self._no_effort.add(model)
            return True
        return False

    @staticmethod
    def tools(defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": d["name"],
                "description": d["description"],
                "input_schema": d["parameters"],
                "eager_input_streaming": True,
            }
            for d in defs
        ]

    async def reply(
        self,
        model: str,
        system: str,
        history: list[dict[str, Any]],
        context: str,
        said: str,
        tool_defs: list[dict[str, Any]],
        run_tool: ToolRunner,
        out: SentenceStream,
    ) -> AsyncIterator[str]:
        messages: list[dict[str, Any]] = [
            *history,
            {"role": "user", "content": [{"type": "text", "text": context}, {"type": "text", "text": said}]},
        ]
        tools = self.tools(tool_defs)
        async with self._errors(model):
            for _round in range(MAX_TOOL_ROUNDS):
                for _attempt in range(2):
                    spoke = len(out.spoken)
                    try:
                        async with self.client.messages.stream(
                            model=model,
                            max_tokens=1024,
                            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                            tools=tools,
                            messages=messages,
                            **self._opts(model),
                        ) as stream:
                            async for event in stream:
                                if event.type == "text":
                                    for sentence in out.feed(event.text):
                                        yield sentence
                            final = await stream.get_final_message()
                        break
                    except Exception as e:
                        if len(out.spoken) == spoke and self._effort_rejected(e, model):
                            continue
                        raise
                for sentence in out.flush():
                    yield sentence
                if final.stop_reason == "refusal":
                    yield "Say again?"
                    return
                uses = [b for b in final.content if b.type == "tool_use"]
                if final.stop_reason != "tool_use" or not uses:
                    return
                messages.append({"role": "assistant", "content": final.content})
                results = []
                for b in uses:
                    text, is_error = run_tool(b.name, b.input if isinstance(b.input, dict) else {})
                    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": b.id, "content": text}
                    if is_error:
                        result["is_error"] = True
                    results.append(result)
                messages.append({"role": "user", "content": results})

    async def complete(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        async with self._errors(model):
            for _attempt in range(2):
                try:
                    msg = await self.client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                        messages=[{"role": "user", "content": prompt}],
                        **self._opts(model),
                    )
                    break
                except Exception as e:
                    if self._effort_rejected(e, model):
                        continue
                    raise
        if msg.stop_reason == "refusal":
            raise LLMError("The model declined to answer.")
        return " ".join(b.text for b in msg.content if b.type == "text")

    async def list_models(self) -> list[str]:
        async with self._errors(""):
            page = await self.client.models.list(limit=100)
            return [m.id for m in page.data]

    async def aclose(self) -> None:
        await self.client.close()

    def _errors(self, model: str) -> _AnthropicErrors:
        return _AnthropicErrors(model)


class _AnthropicErrors:
    """Translate anthropic SDK exceptions into LLMError with plain-English messages."""

    def __init__(self, model: str) -> None:
        self.model = model

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        if exc is None or isinstance(exc, (LLMError, asyncio.CancelledError, GeneratorExit)):
            return False
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            raise LLMError("The Anthropic API key was rejected. Check it in Settings > AI.", fatal=True) from exc
        if isinstance(exc, anthropic.PermissionDeniedError):
            raise LLMError(f"This Anthropic key can't use {self.model}.", fatal=True) from exc
        if isinstance(exc, anthropic.NotFoundError):
            raise LLMError(
                f"Model {self.model} wasn't found on Anthropic. Pick another model in Settings > AI.", fatal=True
            ) from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise LLMError("Rate limited by Anthropic.") from exc
        if isinstance(exc, anthropic.BadRequestError):
            msg = getattr(exc, "message", str(exc))
            if "credit" in msg.lower() or "billing" in msg.lower():
                raise LLMError("Your Anthropic account is out of credit.", fatal=True) from exc
            raise LLMError(f"Anthropic rejected the request: {msg}") from exc
        if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
            raise LLMError("Can't reach Anthropic (offline?).") from exc
        if isinstance(exc, anthropic.APIStatusError):
            raise LLMError(f"Anthropic API error {exc.status_code}.") from exc
        return False


# =================================================================================================
# OpenAI-style chat completions (OpenAI, Gemini, OpenRouter, Poe, Ollama, LM Studio, custom)
# =================================================================================================


class HTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:300] or "no details"
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("msg") or err)[:300]
        return str(err)[:300]
    return str(data)[:300]


class OpenAICompatBackend:
    """Chat completions over plain HTTP (aiohttp), with streaming and tool calls.

    Services disagree on the details, so requests adapt: when a server rejects a parameter
    (tools, reasoning_effort, max_completion_tokens…) we drop or swap it, remember that for the
    model, and retry, rather than failing the driver's question.
    """

    def __init__(self, provider: Provider, key: str, base_url: str) -> None:
        self.provider = provider
        self.key = key
        self.base_url = base_url.rstrip("/")
        self._session: Any = None
        self._session_loop: asyncio.AbstractEventLoop | None = None
        self._quirks: dict[str, set[str]] = {}  # model -> adjustments that worked
        self._auto_model: str | None = None

    # ---- HTTP ---------------------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        if self.provider.id == "openrouter":
            h["HTTP-Referer"] = "https://github.com/ethandadev/box-box"
            h["X-Title"] = "Box Box"
        return h

    def _client(self) -> Any:
        import aiohttp

        loop = asyncio.get_running_loop()
        if self._session is None or self._session.closed or self._session_loop is not loop:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60, connect=5, sock_read=30))
            self._session_loop = loop
        return self._session

    def _where(self) -> str:
        return self.provider.label if not (self.provider.local or self.provider.id == "custom") else self.base_url

    def _net_error(self, e: Exception) -> LLMError:
        import aiohttp

        if isinstance(e, aiohttp.ClientConnectorError):
            if self.provider.local:
                name = self.provider.label.split(" (")[0]
                return LLMError(f"Can't reach {name} at {self.base_url}. Is it running?")
            return LLMError(f"Can't reach {self._where()} (offline?).")
        if isinstance(e, (TimeoutError, aiohttp.ServerTimeoutError)):
            return LLMError(f"{self._where()} took too long to answer.")
        return LLMError(f"Connection to {self._where()} failed: {e}")

    async def _call(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        """A JSON request. Raises HTTPError for error statuses, LLMError for network trouble."""
        import aiohttp

        try:
            async with self._client().request(method, self.base_url + path, json=body, headers=self._headers()) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise HTTPError(resp.status, _error_message(text))
        except (aiohttp.ClientError, TimeoutError) as e:
            raise self._net_error(e) from e
        try:
            return json.loads(text)
        except ValueError as e:
            raise LLMError(f"{self._where()} sent something that isn't JSON: {text[:120]!r}") from e

    async def _chunks(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Server-sent events from a streaming chat completion."""
        import aiohttp

        try:
            async with self._client().post(
                self.base_url + "/chat/completions", json=body, headers=self._headers()
            ) as resp:
                if resp.status >= 400:
                    raise HTTPError(resp.status, _error_message(await resp.text()))
                async for raw in resp.content:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(chunk, dict) and chunk.get("error"):
                        raise HTTPError(400, _error_message(data))
                    if isinstance(chunk, dict):
                        yield chunk
        except (aiohttp.ClientError, TimeoutError) as e:
            raise self._net_error(e) from e

    def reset(self) -> None:
        """Forget what we learned about the server (the loaded model may have changed)."""
        self._auto_model = None

    def _fail(self, e: HTTPError, model: str) -> LLMError:
        if e.status == 404:
            self._auto_model = None  # the local server may have a different model loaded now
        label, msg, low = self.provider.label, e.message, e.message.lower()
        if e.status == 401:
            return LLMError(f"The {label} API key was rejected. Check it in Settings > AI.", fatal=True)
        if e.status == 402 or "credit" in low or "billing" in low or "insufficient" in low:
            return LLMError(f"Your {label} account is out of credit ({msg}).", fatal=True)
        if e.status == 403:
            return LLMError(f"{label} refused: {msg}", fatal=True)
        if e.status == 404 or ("model" in low and ("not found" in low or "does not exist" in low or "invalid" in low)):
            return LLMError(
                f"Model '{model}' wasn't found on {self._where()}. Check the model in Settings > AI.", fatal=True
            )
        if e.status == 429:
            return LLMError(f"Rate limited by {label}: {msg}")
        return LLMError(f"{label} error {e.status}: {msg}")

    # ---- request shaping ----------------------------------------------------------------------------

    def _reasoning_effort(self, model: str) -> str | None:
        m = model.lower().split("/")[-1]
        if self.provider.id == "openai" and (m.startswith(("gpt-5", "o1", "o3", "o4"))):
            return "minimal" if m.startswith("gpt-5") and not m.startswith("gpt-5.") else "low"
        if self.provider.id == "gemini" and "2.5-pro" not in m and m.startswith("gemini-2.5"):
            return "none"  # thinking off: radio answers need speed, not essays
        return None

    def _body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, Any]:
        q = self._quirks.get(model, set())
        body: dict[str, Any] = {"model": model, "messages": messages}
        if stream:
            body["stream"] = True
        use_completion_tokens = self.provider.id == "openai"
        if "swap_max_tokens" in q:
            use_completion_tokens = not use_completion_tokens
        if "no_max_tokens" not in q:
            body["max_completion_tokens" if use_completion_tokens else "max_tokens"] = max_tokens
        if tools and "no_tools" not in q:
            body["tools"] = tools
        effort = self._reasoning_effort(model)
        if effort and "no_effort" not in q:
            body["reasoning_effort"] = "low" if "effort_low" in q else effort
        return body

    def _adapt(self, model: str, e: HTTPError, body: dict[str, Any]) -> bool:
        """Learn from a 400 about an unsupported parameter. True if a retry is worth it."""
        if e.status not in (400, 422):
            return False
        low = e.message.lower()
        q = self._quirks.setdefault(model, set())
        before = set(q)
        if "reasoning_effort" in body and ("reasoning" in low or "effort" in low):
            if body["reasoning_effort"] != "low" and "effort_low" not in q:
                q.add("effort_low")
            else:
                q.add("no_effort")
        elif ("max_tokens" in low or "max_completion_tokens" in low) and "swap_max_tokens" not in q:
            q.add("swap_max_tokens")
        elif "max_tokens" in low or "max_completion_tokens" in low:
            q.add("no_max_tokens")
        elif "tools" in body and ("tool" in low or "function" in low):
            q.add("no_tools")
        if q != before:
            log.info("%s %s: adjusting request after: %s (%s)", self.provider.id, model, e.message, sorted(q))
            return True
        return False

    async def resolve_model(self, model: str) -> str:
        """An empty model means 'whatever the local server has loaded'."""
        if model:
            return model
        if self._auto_model is None:
            models = await self.list_models()
            if not models:
                raise LLMError(
                    f"No model is loaded on {self._where()}. Load one, or type a model name in Settings > AI.",
                    fatal=True,
                )
            self._auto_model = models[0]
            log.info("using %s's first model: %s", self.provider.label, self._auto_model)
        return self._auto_model

    @staticmethod
    def tools(defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"type": "function", "function": d} for d in defs]

    # ---- the three operations -------------------------------------------------------------------------

    async def reply(
        self,
        model: str,
        system: str,
        history: list[dict[str, Any]],
        context: str,
        said: str,
        tool_defs: list[dict[str, Any]],
        run_tool: ToolRunner,
        out: SentenceStream,
    ) -> AsyncIterator[str]:
        model = await self.resolve_model(model)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": f"{context}\n{said}"},
        ]
        tools = self.tools(tool_defs)
        for _round in range(MAX_TOOL_ROUNDS):
            calls: dict[int, dict[str, str]] = {}
            text_parts: list[str] = []
            for attempt in range(4):
                body = self._body(model, messages, tools, 1024, stream=True)
                got_any = False
                try:
                    async for chunk in self._chunks(body):
                        got_any = True
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                text_parts.append(content)
                                for sentence in out.feed(content):
                                    yield sentence
                            for tc in delta.get("tool_calls") or []:
                                if "index" in tc:
                                    idx = int(tc["index"])
                                else:
                                    # No index (some servers send each whole call in its own chunk):
                                    # a new id means a new call, otherwise it continues the last one.
                                    last = max(calls) if calls else -1
                                    new_id = tc.get("id") and (last < 0 or calls[last]["id"] not in ("", tc["id"]))
                                    idx = last + 1 if new_id or last < 0 else last
                                entry = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                                if tc.get("id"):
                                    entry["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name") and not entry["name"]:
                                    entry["name"] = fn["name"]
                                if fn.get("arguments"):
                                    args = fn["arguments"]
                                    entry["args"] += args if isinstance(args, str) else json.dumps(args)
                    break
                except HTTPError as e:
                    if not got_any and attempt < 3 and self._adapt(model, e, body):
                        continue
                    raise self._fail(e, model) from e
            for sentence in out.flush():
                yield sentence
            if not calls:
                return
            ordered = [calls[k] for k in sorted(calls)]
            for n, c in enumerate(ordered):
                c["id"] = c["id"] or f"call_{_round}_{n}"
            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["args"] or "{}"},
                        }
                        for c in ordered
                    ],
                }
            )
            for c in ordered:
                try:
                    args = json.loads(c["args"]) if c["args"].strip() else {}
                except ValueError:
                    args = {}
                text, _is_error = run_tool(c["name"], args if isinstance(args, dict) else {})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": text})

    async def complete(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        model = await self.resolve_model(model)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

        for attempt in range(4):
            body = self._body(model, messages, None, max_tokens, stream=False)
            try:
                data = await self._call("POST", "/chat/completions", body)
                break
            except HTTPError as e:
                if attempt < 3 and self._adapt(model, e, body):
                    continue
                raise self._fail(e, model) from e
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(f"{self._where()} sent an answer I couldn't read.") from e
        out = SentenceStream()
        return " ".join(out.feed(content) + out.flush())

    async def list_models(self) -> list[str]:
        try:
            data = await self._call("GET", "/models", None)
        except HTTPError as e:
            raise self._fail(e, "") from e
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        ids = []
        for m in items or []:
            mid = m.get("id") or m.get("name") if isinstance(m, dict) else m
            if isinstance(mid, str) and mid:
                ids.append(mid.removeprefix("models/"))
        return ids

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def make_backend(provider: Provider, key: str, base_url: str) -> AnthropicBackend | OpenAICompatBackend:
    if provider.kind == "anthropic":
        return AnthropicBackend(provider, key, base_url)
    return OpenAICompatBackend(provider, key, base_url)
