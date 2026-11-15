"""Free-first model selection (same scheme as Parts A and B, env prefix BUGASSIST_).

  gemini      Gemini API free tier. Free key from https://aistudio.google.com/apikey (no card).
  ollama      Local open models, free and offline. Needs Ollama running (ollama.com).
  groq        Groq free tier through LiteLLM. Needs GROQ_API_KEY.
  openrouter  OpenRouter ":free" models through LiteLLM. Needs OPENROUTER_API_KEY.
  litellm     Anything LiteLLM supports: set BUGASSIST_MODEL / BUGASSIST_RESEARCH_MODEL.
"""

from __future__ import annotations

import os
from typing import Any

from google.genai import types

DEFAULTS: dict[str, tuple[str, str]] = {
    # provider: (triage agent, research agent)
    "gemini": ("gemini-2.5-flash", "gemini-2.5-flash"),
    "ollama": ("ollama_chat/qwen3:8b", "ollama_chat/qwen3:8b"),
    "groq": ("groq/llama-3.3-70b-versatile", "groq/llama-3.3-70b-versatile"),
    "openrouter": (
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    ),
    "litellm": ("", ""),
}


def provider() -> str:
    p = os.getenv("BUGASSIST_PROVIDER", "gemini").lower()
    if p not in DEFAULTS:
        raise ValueError(f"BUGASSIST_PROVIDER must be one of {sorted(DEFAULTS)}, got {p!r}")
    return p


def is_gemini() -> bool:
    return provider() == "gemini"


def make_model(role: str = "triage") -> Any:
    """A Gemini model name, or a LiteLlm instance for the other providers."""
    prov = provider()
    default = DEFAULTS[prov][0 if role == "triage" else 1]
    name = os.getenv("BUGASSIST_MODEL" if role == "triage" else "BUGASSIST_RESEARCH_MODEL", default)
    if not name:
        raise ValueError("BUGASSIST_PROVIDER=litellm requires BUGASSIST_MODEL / BUGASSIST_RESEARCH_MODEL")
    if prov == "gemini":
        return name
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover
        raise ImportError('Non-Gemini providers need LiteLLM: pip install -e ".[local]"') from exc
    kwargs: dict[str, Any] = {}
    if prov == "ollama":
        kwargs["api_base"] = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
    return LiteLlm(model=name, **kwargs)


def generation_config() -> types.GenerateContentConfig | None:
    """Retry/backoff for Gemini free-tier rate limits (HTTP 429/503). None for other backends."""
    if not is_gemini():
        return None
    return types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=6, initial_delay=2.0, max_delay=60.0, exp_base=2.0)
        )
    )
