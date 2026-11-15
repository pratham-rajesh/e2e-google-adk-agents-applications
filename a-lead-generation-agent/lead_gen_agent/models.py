"""Free-first model selection.

Pick a backend with LEADGEN_PROVIDER (default `gemini`):

  gemini      Gemini API free tier. Free key from https://aistudio.google.com/apikey (no card).
  ollama      Local open models, fully free and offline. Needs Ollama running (ollama.com).
  groq        Groq free tier (Llama etc.) through LiteLLM. Needs GROQ_API_KEY.
  openrouter  OpenRouter ":free" models through LiteLLM. Needs OPENROUTER_API_KEY.
  litellm     Anything LiteLLM supports: set LEADGEN_FAST_MODEL / LEADGEN_SMART_MODEL.

Non-Gemini backends use LiteLLM (`pip install -e ".[local]"`). The "fast" role is used for
research/extraction/evaluation, the "smart" role for the final report; on free backends both
default to the same model.
"""

from __future__ import annotations

import os
from typing import Any

from google.genai import types

DEFAULTS: dict[str, tuple[str, str]] = {
    # provider: (fast, smart)
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
    p = os.getenv("LEADGEN_PROVIDER", "gemini").lower()
    if p not in DEFAULTS:
        raise ValueError(f"LEADGEN_PROVIDER must be one of {sorted(DEFAULTS)}, got {p!r}")
    return p


def is_gemini() -> bool:
    return provider() == "gemini"


def make_model(role: str = "fast") -> Any:
    """Return a model name (Gemini) or a LiteLlm instance for the configured provider."""
    prov = provider()
    default = DEFAULTS[prov][0 if role == "fast" else 1]
    name = os.getenv("LEADGEN_FAST_MODEL" if role == "fast" else "LEADGEN_SMART_MODEL", default)
    if not name:
        raise ValueError("LEADGEN_PROVIDER=litellm requires LEADGEN_FAST_MODEL / LEADGEN_SMART_MODEL")
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
