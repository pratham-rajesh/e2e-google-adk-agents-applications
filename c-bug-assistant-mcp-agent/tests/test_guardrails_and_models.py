"""Pure-function tests: secret detection, MCP result unwrapping, provider selection, search backend choice."""

import json

import pytest

from bug_assistant import models
from bug_assistant.guardrails import find_secret, unwrap
from bug_assistant.tools import search


@pytest.mark.parametrize("text", [
    "-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
    "key AKIAABCDEFGHIJKLMNOP leaked",
    "AIzaSyA-1234567890abcdefghijklmnopqrstu",
    "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    "password = hunter2hunter2",
    'api_key: "abcd1234efgh5678"',
    "sk-proj-abcdefghijklmnopqrstuvwxyz",
    "webhook_secret = whsec_live_8f2a91c4d7e3b6a0",     # prefixed names: the real-world shape
    "export DB_PASSWORD=correcthorsebattery",
    "STRIPE_KEY sk_live_51Habcdefghijklmnop",
])
def test_secrets_detected(text):
    assert find_secret(text)


@pytest.mark.parametrize("text", [
    "NullPointerException in PriceCalculator.lineTotal",
    "password = <redacted>",
    "api_key: ****",
    "webhook_secret = <redacted>",
    "The token expired after 30 minutes",         # the word alone is not a secret
    "set password to a longer one",
    "",
])
def test_ordinary_text_is_not_flagged(text):
    assert find_secret(text) is None


def test_unwrap_handles_every_shape_adk_can_deliver():
    payload = {"status": "ok", "count": 1}
    assert unwrap({"content": [{"type": "text", "text": json.dumps(payload)}]}) == payload
    assert unwrap({"structuredContent": payload, "content": []}) == payload
    assert unwrap({"error": "This tool call requires confirmation"}) == {"error": "This tool call requires confirmation"}
    assert unwrap({"content": [{"type": "text", "text": "not json"}]}) == {"content": [{"type": "text", "text": "not json"}]}
    assert unwrap("garbage") == {}


def test_default_provider_is_gemini_free_tier(monkeypatch):
    monkeypatch.delenv("BUGASSIST_PROVIDER", raising=False)
    assert models.provider() == "gemini" and models.make_model() == "gemini-2.5-flash"
    assert models.generation_config().http_options.retry_options.attempts == 6


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("BUGASSIST_PROVIDER", "nope")
    with pytest.raises(ValueError, match="BUGASSIST_PROVIDER"):
        models.provider()


def test_ollama_goes_through_litellm_and_has_no_retry_config(monkeypatch):
    monkeypatch.setenv("BUGASSIST_PROVIDER", "ollama")
    model = models.make_model("research")
    assert model.model == "ollama_chat/qwen3:8b"
    assert models.generation_config() is None


def test_search_backend_follows_provider(monkeypatch):
    monkeypatch.setenv("BUGASSIST_PROVIDER", "gemini")
    assert search.search_tools()[0].__class__.__name__ == "GoogleSearchTool"
    monkeypatch.setenv("BUGASSIST_PROVIDER", "groq")
    assert search.search_tools() == [search.web_search]
    monkeypatch.setenv("BUGASSIST_SEARCH_BACKEND", "google")
    assert search.search_tools()[0].__class__.__name__ == "GoogleSearchTool"


async def test_web_search_reports_network_failure_as_data(monkeypatch):
    def boom(query, n):
        raise RuntimeError("blocked")

    monkeypatch.setattr(search, "_ddg_search", boom)
    res = await search.web_search("anything")
    assert res["status"] == "error" and res["results"] == []
    monkeypatch.setattr(search, "_ddg_search", lambda q, n: (_ for _ in ()).throw(RuntimeError("No results found")))
    assert (await search.web_search("x"))["status"] == "ok"
