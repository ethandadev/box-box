"""The AI services the engineer can think with, and what each one needs.

Two wire protocols cover everything: Anthropic's own API (via the anthropic SDK), and the OpenAI-style
chat completions API, which OpenAI, Google Gemini, OpenRouter, Poe, Ollama, LM Studio and most other
services speak. Adding a provider is one entry here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # "anthropic" | "openai" (chat completions)
    base_url: str  # default address; "" means the user must give one
    default_model: str  # "" means "whatever the server has loaded"
    models: tuple[str, ...]  # suggestions shown in the model box
    key_field: str = ""  # AISettings field holding this provider's key
    key_env: tuple[str, ...] = ()  # environment variables checked when the field is empty
    key_url: str = ""  # where to get a key
    key_hint: str = ""  # what a key looks like, for the placeholder
    needs_key: bool = True
    local: bool = False  # runs on this PC: no key, no cost, needs the server running
    note: str = ""  # one line of advice for the setup page


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            "anthropic",
            "Anthropic Claude",
            "anthropic",
            "https://api.anthropic.com",
            "claude-haiku-5-5",
            ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"),
            key_field="api_key",
            key_env=("ANTHROPIC_API_KEY",),
            key_url="https://console.anthropic.com/settings/keys",
            key_hint="sk-ant-…",
            note="Haiku answers fastest on the radio. Sonnet thinks harder but takes a moment longer.",
        ),
        Provider(
            "openai",
            "OpenAI",
            "openai",
            "https://api.openai.com/v1",
            "gpt-5-mini",
            ("gpt-5-mini", "gpt-5-nano", "gpt-5", "gpt-4.1-mini", "gpt-4.1"),
            key_field="openai_key",
            key_env=("OPENAI_API_KEY",),
            key_url="https://platform.openai.com/api-keys",
            key_hint="sk-…",
            note="Mini models are the sweet spot for radio: quick and cheap.",
        ),
        Provider(
            "gemini",
            "Google Gemini",
            "openai",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-2.5-flash",
            ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"),
            key_field="gemini_key",
            key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            key_url="https://aistudio.google.com/apikey",
            key_hint="AIza…",
            note="Flash is fast and has a free tier.",
        ),
        Provider(
            "openrouter",
            "OpenRouter",
            "openai",
            "https://openrouter.ai/api/v1",
            "google/gemini-2.5-flash",
            (
                "google/gemini-2.5-flash",
                "anthropic/claude-haiku-4.5",
                "openai/gpt-5-mini",
                "x-ai/grok-4-fast",
                "meta-llama/llama-3.3-70b-instruct",
            ),
            key_field="openrouter_key",
            key_env=("OPENROUTER_API_KEY",),
            key_url="https://openrouter.ai/settings/keys",
            key_hint="sk-or-…",
            note="One key for hundreds of models. Use the model's full id, like google/gemini-2.5-flash.",
        ),
        Provider(
            "poe",
            "Poe",
            "openai",
            "https://api.poe.com/v1",
            "Gemini-2.5-Flash",
            ("Gemini-2.5-Flash", "Claude-Haiku-4.5", "GPT-5-mini", "Claude-Sonnet-4.5", "GPT-5"),
            key_field="poe_key",
            key_env=("POE_API_KEY",),
            key_url="https://poe.com/api_key",
            key_hint="",
            note="Uses your Poe points. Model names are Poe bot names, like Claude-Haiku-4.5.",
        ),
        Provider(
            "ollama",
            "Ollama (on this PC)",
            "openai",
            "http://localhost:11434/v1",
            "",
            ("qwen3:14b", "qwen3:8b", "llama3.1:8b", "mistral-nemo", "gpt-oss:20b"),
            needs_key=False,
            local=True,
            note="Free and private. Pick a model that supports tools, e.g. 'ollama pull qwen3:14b'.",
        ),
        Provider(
            "lmstudio",
            "LM Studio (on this PC)",
            "openai",
            "http://localhost:1234/v1",
            "",
            (),
            needs_key=False,
            local=True,
            note="Load a model and start the server in LM Studio's Developer tab. Empty model uses the loaded one.",
        ),
        Provider(
            "custom",
            "Other (OpenAI-compatible)",
            "openai",
            "",
            "",
            (),
            key_field="custom_key",
            needs_key=False,
            note="Any server with an OpenAI-style /chat/completions endpoint: vLLM, Groq, Together, DeepSeek, Mistral…",
        ),
    )
}

DEFAULT = "anthropic"


def get(provider_id: str) -> Provider:
    return PROVIDERS.get(provider_id) or PROVIDERS[DEFAULT]


def choices() -> list[tuple[str, str]]:
    return [(p.id, p.label) for p in PROVIDERS.values()]


def key_for(provider: Provider, ai: object) -> str:
    """The provider's API key: from settings, else from its environment variables."""
    if provider.key_field:
        value = str(getattr(ai, provider.key_field, "") or "").strip()
        if value:
            return value
    for env in provider.key_env:
        value = os.environ.get(env, "").strip()
        if value:
            return value
    return ""


def base_url_for(provider: Provider, ai: object) -> str:
    url = str(getattr(ai, "base_url", "") or "").strip() if provider.local or provider.id == "custom" else ""
    return (url or provider.base_url).rstrip("/")


def model_for(provider: Provider, ai: object) -> str:
    return str(getattr(ai, "model", "") or "").strip() or provider.default_model


def missing(provider: Provider, ai: object) -> str | None:
    """What's stopping this provider from working, in plain English, or None if it's configured."""
    if provider.needs_key and not key_for(provider, ai):
        return f"No {provider.label} API key."
    if not base_url_for(provider, ai):
        return "No server address set."
    return None


def public() -> list[dict[str, object]]:
    """Provider facts for the UI."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "default_model": p.default_model,
            "models": list(p.models),
            "key_field": p.key_field,
            "key_url": p.key_url,
            "key_hint": p.key_hint,
            "needs_key": p.needs_key,
            "local": p.local,
            "base_url": p.base_url,
            "note": p.note,
        }
        for p in PROVIDERS.values()
    ]
