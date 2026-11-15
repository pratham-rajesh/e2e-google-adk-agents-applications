import json

import pytest
from google.adk.runners import InMemoryRunner
from google.adk.tools import google_search
from google.genai import types

from lead_gen_agent import models
from lead_gen_agent.config import K
from lead_gen_agent.pipeline import build_root_agent
from lead_gen_agent.stages import parse_json_object
from lead_gen_agent.tools import search, web

from .fake_llm import FakeLlm
from .test_pipeline_e2e import ROUND1, ROUND2, _models, _offline  # noqa: F401  (autouse fixture)


def test_default_provider_is_gemini_with_free_tier_model(monkeypatch):
    monkeypatch.delenv("LEADGEN_PROVIDER", raising=False)
    monkeypatch.delenv("LEADGEN_FAST_MODEL", raising=False)
    assert models.make_model("fast") == "gemini-2.5-flash"
    assert models.generation_config().http_options.retry_options.attempts >= 5


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("LEADGEN_PROVIDER", "nope")
    with pytest.raises(ValueError):
        models.provider()


@pytest.mark.parametrize("prov,expected", [
    ("ollama", "ollama_chat/qwen3:8b"),
    ("groq", "groq/llama-3.3-70b-versatile"),
    ("openrouter", "openrouter/meta-llama/llama-3.3-70b-instruct:free"),
])
def test_litellm_backends(monkeypatch, prov, expected):
    monkeypatch.setenv("LEADGEN_PROVIDER", prov)
    monkeypatch.delenv("LEADGEN_FAST_MODEL", raising=False)
    m = models.make_model("fast")
    assert m.model == expected
    assert models.generation_config() is None


def test_model_override_env(monkeypatch):
    monkeypatch.setenv("LEADGEN_PROVIDER", "ollama")
    monkeypatch.setenv("LEADGEN_FAST_MODEL", "ollama_chat/llama3.1:8b")
    assert models.make_model("fast").model == "ollama_chat/llama3.1:8b"


def test_search_backend_selection(monkeypatch):
    monkeypatch.delenv("LEADGEN_SEARCH_BACKEND", raising=False)
    monkeypatch.setenv("LEADGEN_PROVIDER", "gemini")
    assert search.search_tools() == [google_search]
    monkeypatch.setenv("LEADGEN_PROVIDER", "ollama")
    assert search.search_tools() == [search.web_search]
    monkeypatch.setenv("LEADGEN_SEARCH_BACKEND", "ddg")
    monkeypatch.setenv("LEADGEN_PROVIDER", "gemini")
    assert search.search_tools() == [search.web_search]


async def test_web_search_tool_shapes_results_and_clamps(monkeypatch):
    seen = {}

    def fake(query, n):
        seen["n"] = n
        return [{"title": "T", "url": "https://x.example", "snippet": "S"}]

    monkeypatch.setattr(search, "_ddg_search", fake)
    out = await search.web_search("q", max_results=99)
    assert out["status"] == "ok" and out["results"][0]["url"] == "https://x.example" and seen["n"] == 10


async def test_web_search_tool_reports_errors(monkeypatch):
    def boom(q, n):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(search, "_ddg_search", boom)
    out = await search.web_search("q")
    assert out["status"] == "error" and "rate limited" in out["message"]


@pytest.mark.parametrize("text", [
    '{"leads": []}',
    '```json\n{"leads": []}\n```',
    'Sure! Here you go:\n{"leads": []}\nHope that helps.',
])
def test_parse_json_object_is_lenient(text):
    assert parse_json_object(text) == {"leads": []}


def test_parse_json_object_garbage():
    assert parse_json_object("no json here") == {}
    assert parse_json_object("[1,2]") == {}


async def test_full_flow_on_non_gemini_provider_with_fenced_json(monkeypatch):
    """Ollama/Groq-style run: no output_schema, extractor wraps JSON in a code fence."""
    monkeypatch.setenv("LEADGEN_PROVIDER", "ollama")
    m = _models()
    m.extractor = FakeLlm(script=[
        "```json\n" + json.dumps(ROUND1) + "\n```",
        "Here is the JSON:\n" + json.dumps(ROUND2),
    ])
    runner = InMemoryRunner(agent=build_root_agent(m), app_name="prov")
    session = await runner.session_service.create_session(app_name="prov", user_id="u")
    for msg in ("Find customers for my fraud API", "go"):
        async for _ in runner.run_async(user_id="u", session_id=session.id,
                                        new_message=types.Content(role="user", parts=[types.Part(text=msg)])):
            pass
    state = (await runner.session_service.get_session(app_name="prov", user_id="u", session_id=session.id)).state
    assert [l["company_name"] for l in state[K.RANKED]][0] == "PayNova"
    assert len(state[K.RANKED]) == 3
    assert "output_schema" not in str(m.extractor.requests[0].config.response_schema or "")
    assert "ONLY a JSON object" in m.extractor.system_text(0)


async def test_web_search_no_results_is_a_hint_not_an_error(monkeypatch):
    def none(q, n):
        raise RuntimeError("No results found.")

    monkeypatch.setattr(search, "_ddg_search", none)
    out = await search.web_search("very narrow query")
    assert out["status"] == "ok" and out["results"] == [] and "broader" in out["hint"]
