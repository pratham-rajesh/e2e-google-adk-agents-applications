from fastapi.testclient import TestClient

from gemini_cli_agent.pipeline import Models, build_pipeline
from server import create_app

from .fake_llm import FakeLlm, call


def _factory():
    return build_pipeline(Models(
        planner=FakeLlm(script=["GOAL: g\nCLI TASK: list files"]),
        worker=FakeLlm(script=[call("gemini_cli", task="list files", subdirectory="proj"), "listed"]),
        reviewer=FakeLlm(script=["VERDICT: PASS\nFINAL ANSWER: all good"]),
    ))


def client() -> TestClient:
    return TestClient(create_app(pipeline_factory=_factory, with_adk_api=False))


def test_healthz():
    r = client().get("/healthz")
    assert r.status_code == 200 and r.json()["approval_mode"] == "default"


def test_analyze():
    r = client().post("/analyze", json={"request": "list the files please"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "PASS" and body["answer"] == "all good"
    assert body["cli_calls"][0]["status"] == "ok"


def test_analyze_validation():
    assert client().post("/analyze", json={"request": "x"}).status_code == 422
    assert client().post("/analyze", json={}).status_code == 422


def test_token_required_when_configured(monkeypatch):
    monkeypatch.setenv("GEMCLI_SERVER_TOKEN", "s3cret")
    c = client()
    body = {"request": "list the files please"}
    assert c.post("/analyze", json=body).status_code == 401
    assert c.post("/analyze", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/analyze", json=body, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/healthz").status_code == 200  # health stays open for load balancers


def test_full_app_includes_adk_routes():
    app = create_app(pipeline_factory=_factory)
    paths = {r.path for r in app.routes}
    assert "/analyze" in paths and "/list-apps" in paths
