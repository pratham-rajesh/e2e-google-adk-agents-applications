"""The real ADK graph (capture -> plan -> worker+tools -> review -> record) with scripted LLMs, a
fake `gemini` executable and a stubbed `git clone`. Only the LLM calls and the network are fake."""

import json
import os
from pathlib import Path

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types

from gemini_cli_agent.config import K
from gemini_cli_agent.pipeline import Models, build_pipeline
from gemini_cli_agent.runner import final_answer, parse_verdict, run_once
from gemini_cli_agent.tools import workspace

from .fake_llm import FakeLlm, call

PLAN = ("GOAL: explain the repo\nREPOSITORY: https://github.com/acme/widgets\n"
        "CLI TASK: Summarise the architecture of {this} repo; cite file paths.\nSUCCESS CRITERIA:\n- cites files")
REVIEW = ("VERDICT: PASS\nCHECKS: cites files - met\nISSUES: none\n"
          "FINAL ANSWER: The repo has one module. (Source: Gemini CLI)")


@pytest.fixture(autouse=True)
def _stub_git(monkeypatch):
    class P:
        returncode = 0

        async def communicate(self):
            return b"", b""

    real_exec = workspace.asyncio.create_subprocess_exec  # asyncio is global: only intercept `git`

    async def fake_exec(*argv, **kw):
        if "git" not in os.path.basename(argv[0]).lower():
            return await real_exec(*argv, **kw)
        os.makedirs(os.path.join(argv[-1], ".git"))
        return P()

    monkeypatch.setattr(workspace.asyncio, "create_subprocess_exec", fake_exec)


def _models(worker_script):
    return Models(
        planner=FakeLlm(script=[PLAN]),
        worker=FakeLlm(script=worker_script),
        reviewer=FakeLlm(script=[REVIEW]),
    )


HAPPY_WORKER = [
    call("clone_repository", repo_url="https://github.com/acme/widgets"),
    lambda req: call("gemini_cli", task="Summarise the architecture of this repo; cite file paths.",
                     subdirectory="repos/acme__widgets"),
    "Gemini CLI says: one module.\nSource: Gemini CLI, repos/acme__widgets",
]


async def test_happy_path():
    m = _models(HAPPY_WORKER)
    out = await run_once(build_pipeline(m), "Explain https://github.com/acme/widgets to me, it has {braces}")

    assert out["verdict"] == "PASS"
    assert out["answer"].startswith("The repo has one module")
    [c] = out["cli_calls"]
    assert c["status"] == "ok" and c["subdirectory"] == "repos/acme__widgets" and c["n"] == 1

    # planner prompt contains the request verbatim (braces survived templating)
    assert "it has {braces}" in m.planner.system_text()
    # worker prompt contains the plan; the plan's own {this} did not break anything
    assert "REPOSITORY: https://github.com/acme/widgets" in m.worker.system_text(0)
    assert "{this}" in m.worker.system_text(0)
    # worker made 3 model calls: clone, cli, final text
    assert len(m.worker.requests) == 3
    # the tool result (with the fake CLI's echo) was fed back to the worker
    cli_result = m.worker.requests[2].contents[-1].parts[0].function_response.response
    assert cli_result["status"] == "ok" and "FAKE GEMINI ANSWER" in cli_result["response"]
    # reviewer saw the call log as ground truth, plus the worker's result
    rev = m.reviewer.system_text()
    assert "1. status=ok dir=repos/acme__widgets" in rev
    assert "Gemini CLI says: one module." in rev
    assert "Explain https://github.com/acme/widgets to me" in rev


async def test_run_record_written():
    out = await run_once(build_pipeline(_models(HAPPY_WORKER)), "Explain the repo")
    rec = json.loads(Path(out["run_record"]).read_text(encoding="utf-8"))
    assert rec["request"] == "Explain the repo"
    assert rec["review"].startswith("VERDICT: PASS")
    assert rec["cli_calls"][0]["status"] == "ok", rec["cli_calls"]
    assert rec["plan"] == PLAN


async def test_cli_failure_is_visible_to_reviewer(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "crash")
    worker = [
        call("gemini_cli", task="analyse", subdirectory="proj"),
        "Gemini CLI failed: exit code 1. No analysis available.",
    ]
    m = _models(worker)
    out = await run_once(build_pipeline(m), "Analyse the project")
    assert out["cli_calls"][0]["status"] == "error" and out["cli_calls"][0]["exit_code"] == 1
    assert "status=error" in m.reviewer.system_text()


async def test_sandbox_rejection_flows_back_to_worker():
    worker = [call("gemini_cli", task="steal", subdirectory="../../etc"), "Rejected by the sandbox."]
    m = _models(worker)
    out = await run_once(build_pipeline(m), "x y z")
    assert out["cli_calls"][0]["status"] == "rejected"
    resp = m.worker.requests[1].contents[-1].parts[0].function_response.response
    assert resp["status"] == "rejected"


async def test_call_log_resets_between_runs_in_same_session():
    agent = build_pipeline(_models([call("gemini_cli", task="t", subdirectory="proj"), "done"] * 2))
    runner = InMemoryRunner(agent=agent, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u")
    for _ in range(2):
        async for _ev in runner.run_async(
            user_id="u", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="go")]),
        ):
            pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    assert len(state[K.CLI_CALLS]) == 1  # not 2: RequestCapture cleared it at the start of run 2


def test_verdict_parsing():
    assert parse_verdict(REVIEW) == "PASS"
    assert parse_verdict("verdict: fail\n...") == "FAIL"
    assert parse_verdict("no verdict here") == "UNKNOWN"
    assert final_answer(REVIEW).startswith("The repo has one module")
    assert final_answer("free text") == "free text"
