"""Eval set, HTTP server, model selection and config."""

import pytest
from fastapi.testclient import TestClient

from code_review_assistant import config, evals, models
from code_review_assistant.pipeline import build_root_agent
from code_review_assistant.runner import make_artifact_service
from server import create_app

from . import demo_offline as d


# ---- eval set ------------------------------------------------------------------------------------------------
def test_eval_cases_all_pass():
    cases = evals.load_cases()
    assert len(cases) >= 10
    failures = {n: p for n, p in evals.run_all(cases).items() if p}
    assert not failures, failures


def test_eval_harness_detects_a_wrong_expectation():
    case = {"name": "wrong", "code": "def f():\n    return 1\n",
            "expect": {"rules_present": ["risky-call:eval"], "style_rules": ["E501"], "floor_verdict": "request_changes",
                       "min_complexity": {"f": 5}, "syntax_ok": False}}
    problems = evals.run_case(case)
    assert len(problems) == 5, problems


# ---- model selection / config ----------------------------------------------------------------------------------
def test_default_provider_is_gemini_with_retry_config():
    assert models.provider() == "gemini" and models.is_gemini()
    assert models.make_model("fast") == "gemini-2.5-flash" and models.make_model("coder") == "gemini-2.5-flash"
    cfg = models.generation_config()
    assert cfg.http_options.retry_options.attempts == 6


def test_model_overrides_per_role(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setenv("CODEREVIEW_CODER_MODEL", "gemini-2.5-pro")
    assert (models.make_model("fast"), models.make_model("coder")) == ("gemini-2.5-flash-lite", "gemini-2.5-pro")


def test_ollama_goes_through_litellm_with_the_chat_prefix(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_PROVIDER", "ollama")
    m = models.make_model("coder")
    assert type(m).__name__ == "LiteLlm" and m.model == "ollama_chat/qwen3:8b"
    assert models.generation_config() is None


def test_unknown_provider_and_missing_litellm_model_are_errors(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_PROVIDER", "openai")
    with pytest.raises(ValueError, match="CODEREVIEW_PROVIDER"):
        models.provider()
    monkeypatch.setenv("CODEREVIEW_PROVIDER", "litellm")
    with pytest.raises(ValueError, match="CODEREVIEW_MODEL"):
        models.make_model("fast")


def test_strict_schema_is_opt_in(monkeypatch):
    def synth(agent):
        return agent.sub_agents[0].sub_agents[1].sub_agents[3]   # review_pipeline -> review_core -> feedback_synthesizer

    agent = d.sample_agent()[0]
    assert synth(agent).name == "feedback_synthesizer" and synth(agent).output_schema is None
    monkeypatch.setenv("CODEREVIEW_STRICT_SCHEMA", "1")
    assert synth(d.sample_agent()[0]).output_schema is not None


def test_config_defaults_and_validation(monkeypatch):
    assert (config.max_code_chars(), config.fix_max_iterations(), config.sandbox_mode()) == (20000, 3, "subprocess")
    assert config.artifact_dir() is None and config.server_token() is None and not config.allow_risky_exec()
    monkeypatch.setenv("CODEREVIEW_SANDBOX", "nonsense")
    with pytest.raises(ValueError):
        config.sandbox_mode()


def test_artifact_service_choice(monkeypatch, tmp_path):
    assert type(make_artifact_service()).__name__ == "InMemoryArtifactService"
    monkeypatch.setenv("CODEREVIEW_ARTIFACT_DIR", str(tmp_path))
    assert type(make_artifact_service()).__name__ == "FileArtifactService"


def test_the_real_root_agent_builds_without_network_or_keys():
    import code_review_assistant

    root = code_review_assistant.agent.root_agent
    assert root.name == "code_review_assistant" and [a.name for a in root.sub_agents] == ["review_pipeline", "fix_pipeline"]
    assert build_root_agent().name == root.name


# ---- HTTP server -----------------------------------------------------------------------------------------------
def client(factory=None, **kw) -> TestClient:
    return TestClient(create_app(agent_factory=factory or (lambda: d.sample_agent()[0]), with_adk_api=False, **kw))


def test_healthz_reports_sandbox_settings():
    r = client().get("/healthz")
    assert r.status_code == 200 and r.json()["sandbox"] == "subprocess" and r.json()["max_code_chars"] == 20000


def test_review_endpoint_returns_verdict_report_and_artifacts():
    r = client().post("/review", json={"code": d.SAMPLE_CODE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "request_changes" and body["report"]["source"] == "model"
    assert body["artifacts"]["review_report.json"] == 1 and body["answer"].startswith("# Code review")
    assert body["fix"] is None


def test_review_endpoint_can_also_fix():
    body = client().post("/review", json={"code": d.SAMPLE_CODE, "fix": True}).json()
    assert body["fix"]["outcome"] == "validated" and "fixed_code.py" in body["artifacts"]


def test_review_endpoint_validation_and_size_limit(monkeypatch):
    c = client()
    assert c.post("/review", json={}).status_code == 422
    assert c.post("/review", json={"code": ""}).status_code == 422
    monkeypatch.setenv("CODEREVIEW_MAX_CODE_CHARS", "40")
    assert c.post("/review", json={"code": d.SAMPLE_CODE}).status_code == 413


def test_bearer_token_is_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_SERVER_TOKEN", "s3cret")
    c = client()
    assert c.post("/review", json={"code": "x = 1"}).status_code == 401
    assert c.post("/review", json={"code": "x = 1"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/healthz").status_code == 200                       # health checks stay open
    ok = c.post("/review", json={"code": d.SAMPLE_CODE}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
