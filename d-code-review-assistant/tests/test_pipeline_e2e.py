"""The real ADK graph with scripted models: routing, every stage, state, artifacts, the sandbox and the fix loop.

Only the LLM replies are scripted. Runner, session state, artifact service, transfers, tools, the sandbox subprocess
and the loop all run for real.
"""

import json

import pytest

from code_review_assistant.config import K
from code_review_assistant.pipeline import Models, build_root_agent
from code_review_assistant.runner import ReviewSession

from . import demo_offline as d
from .fake_llm import FakeLlm, call


def ask_text(code: str) -> str:
    return f"Please review this code:\n```python\n{code}\n```"


def responses_seen(req) -> list[dict]:
    return [p.function_response.response for c in req.contents for p in c.parts or [] if p.function_response]


async def review(agent, code=d.SAMPLE_CODE):
    s = ReviewSession(agent)
    out = await s.ask(ask_text(code))
    return s, out


# ---- review pipeline -----------------------------------------------------------------------------------------
async def test_review_runs_every_stage_in_order_and_produces_a_report():
    agent, fast, coder = d.sample_agent()
    s, out = await review(agent)
    try:
        authors = list(dict.fromkeys(out["authors"]))
        assert authors == ["code_review_assistant", "code_intake", "code_analyzer", "style_checker", "test_runner",
                           "feedback_synthesizer", "report_renderer"]
        rep = out["report"]
        assert rep["verdict"] == "request_changes" and rep["source"] == "model" and rep["round"] == 1
        assert out["answer"].startswith("# Code review (round 1): REQUEST CHANGES")
        st = out["state"]
        assert st[K.INTAKE]["syntax_ok"] and st[K.ANALYSIS]["metrics"]["functions"] == 3
        assert st[K.TEST_RESULT]["status"] == "failed" and st[K.TEST_RESULT]["failed"] == 2
        assert out["artifacts"] == {"generated_tests.py": 1, "review_report.json": 1, "review_report.md": 1}
        saved = json.loads(await s.artifact_text("review_report.json"))
        assert saved["verdict"] == "request_changes" and saved["findings"][0]["severity"] == "high"
    finally:
        await s.close()


async def test_code_travels_through_state_not_through_tool_arguments():
    agent, fast, coder = d.sample_agent()
    s, out = await review(agent)
    try:
        # the analyzer/style tools were called with no arguments at all
        calls = [(p.function_call.name, p.function_call.args) for r in fast.requests[:4] for c in r.contents
                 for p in c.parts or [] if p.function_call and p.function_call.name in ("analyze_code_structure", "check_code_style")]
        assert calls and all(args == {} for _, args in calls)
        # ...and the test-writing stage was shown the code (in its instruction) plus the structure notes
        sys_prompt = coder.system_text(0)
        assert 'API_KEY = "demo-not-a-real-key-4f9a2c71"' in sys_prompt and "Notes: a data-access helper module" in sys_prompt
        assert "{code}" not in sys_prompt and "{structure_notes}" not in sys_prompt
    finally:
        await s.close()


async def test_synthesizer_is_shown_the_deterministic_evidence():
    agent, fast, coder = d.sample_agent()
    s, _ = await review(agent)
    try:
        prompt = coder.system_text(2)  # third coder call = feedback_synthesizer
        assert "TEST RESULT:" in prompt and "{tests}" not in prompt
        assert '"status": "failed"' in prompt and "hardcoded-secret" in prompt and "sql-injection" in prompt
        assert "test_average_of_empty_list_is_zero" in prompt  # the failing test name reached the reviewer
    finally:
        await s.close()


async def test_lenient_model_is_overruled_by_the_policy():
    agent, _, _ = d.risky_agent()
    s, out = await review(agent, d.RISKY_CODE)
    try:
        rep = out["report"]
        assert rep["verdict"] == "request_changes"
        assert {f["severity"] for f in rep["findings"]} == {"critical", "high"}
        assert out["state"][K.TEST_RESULT]["status"] == "refused"
        assert any("raised" in n for n in rep["policy_notes"]) and "Policy notes" in out["answer"]
    finally:
        await s.close()


async def test_unparseable_model_output_falls_back_to_the_deterministic_report():
    agent, _, _ = d.sample_agent(review_json="I think it's mostly fine but who knows!")
    s, out = await review(agent)
    try:
        rep = out["report"]
        assert rep["source"] == "fallback" and rep["verdict"] == "request_changes"
        assert any(f["category"] == "security" for f in rep["findings"])
        assert any(f["category"] == "testing" for f in rep["findings"])  # the failing tests are still reported
    finally:
        await s.close()


async def test_code_with_a_syntax_error_is_still_reviewed_and_not_executed():
    fast = FakeLlm(script=[call("transfer_to_agent", agent_name="review_pipeline"), call("analyze_code_structure"),
                           "Syntax error on line 1.", call("check_code_style"), "E999."])
    coder = FakeLlm(script=[call("run_generated_tests", test_code="from solution import f\n\n\ndef test_f():\n    f()\n"),
                            "Could not run.", d.LENIENT_REVIEW])
    s, out = await review(build_root_agent(Models(fast=fast, coder=coder)), "def f(:\n    return 1\n")
    try:
        assert out["state"][K.INTAKE]["syntax_ok"] is False
        assert out["state"][K.TEST_RESULT]["status"] == "refused"
        assert out["report"]["verdict"] == "request_changes"
        assert out["report"]["findings"][0]["title"] == "Syntax error"
    finally:
        await s.close()


async def test_message_without_code_stops_after_intake():
    fast = FakeLlm(script=[call("transfer_to_agent", agent_name="review_pipeline")])
    coder = FakeLlm(script=["should never be called"])
    s = ReviewSession(build_root_agent(Models(fast=fast, coder=coder)))
    try:
        out = await s.ask("Please review my code, it is great")
        assert "could not find Python code" in out["answer"] and out["report"] is None
        assert coder.requests == [] and len(fast.requests) == 1  # no stage after intake ran
    finally:
        await s.close()


async def test_oversized_submission_is_rejected_at_intake(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_MAX_CODE_CHARS", "50")
    agent, fast, coder = d.sample_agent()
    s, out = await review(agent)
    try:
        assert "limit is 50" in out["answer"] and coder.requests == []
    finally:
        await s.close()


async def test_second_review_in_one_session_resets_state_and_versions_artifacts():
    review_turn = [call("transfer_to_agent", agent_name="review_pipeline"), call("analyze_code_structure"), "n",
                   call("check_code_style"), "n"]
    coder_turn = [call("run_generated_tests", test_code=d.TESTS), "n", d.REVIEW_JSON]
    agent = build_root_agent(Models(fast=FakeLlm(script=review_turn * 2), coder=FakeLlm(script=coder_turn * 2)))
    s = ReviewSession(agent)
    try:
        first = await s.ask(ask_text(d.SAMPLE_CODE))
        second = await s.ask(ask_text("def one():\n    '''x'''\n    return 1\n"))
        assert first["report"]["round"] == 1 and second["report"]["round"] == 2
        assert second["state"][K.CODE].startswith("def one")
        assert second["artifacts"]["review_report.json"] == 2          # two versions kept, not overwritten
        v0, v1 = json.loads(await s.artifact_text("review_report.json", 0)), json.loads(await s.artifact_text("review_report.json", 1))
        assert (v0["round"], v1["round"]) == (1, 2)
    finally:
        await s.close()


# ---- fix pipeline --------------------------------------------------------------------------------------------
async def test_fix_loop_rejects_attempt_one_then_validates_attempt_two():
    agent, fast, coder = d.sample_agent()
    s, _ = await review(agent)
    try:
        out = await s.ask("Please fix the problems you found.")
        st = out["state"]
        assert st[K.FIX_ITER] == 2 and st[K.FIX_PASSED] is True and st[K.FIX_RESULT]["validated_by"] == "tests"
        assert out["fix"]["outcome"] == "validated" and out["fix"]["attempts"] == 2
        assert out["answer"].startswith("# Fix result: VALIDATED") and "-API_KEY" in out["answer"]
        assert st[K.FIXED_CODE].startswith("import os")
        assert out["artifacts"]["fixed_code.py"] == 1 and "fixed_code_UNVERIFIED.py" not in out["artifacts"]
        assert 'os.environ.get("API_KEY"' in await s.artifact_text("fixed_code.py")
        # the second fixer prompt carried the first attempt's rejection back to the model
        fixer_prompt = next(coder.system_text(i) for i in range(len(coder.requests) - 1, -1, -1)
                            if "TESTS THAT MUST PASS" in coder.system_text(i))
        assert "ATTEMPT 1 WAS REJECTED" in fixer_prompt and "test_add_item_does_not_share_state" in fixer_prompt
        assert "Still present: " in fixer_prompt  # attempt 1 still had the hard-coded key
        # loop ended early: attempt 3 was never requested (fixer asked twice + the fix summary)
        assert sum("TESTS THAT MUST PASS" in coder.system_text(i) for i in range(len(coder.requests))) == 2
    finally:
        await s.close()


async def test_review_findings_reach_the_fixer_prompt():
    agent, fast, coder = d.sample_agent()
    s, _ = await review(agent)
    try:
        await s.ask("fix it")
        prompt = next(coder.system_text(i) for i in range(len(coder.requests)) if "TESTS THAT MUST PASS" in coder.system_text(i))
        assert "[high] line 3: Hard-coded API key" in prompt and "def test_average_of_empty_list_is_zero" in prompt
    finally:
        await s.close()


async def test_fix_before_any_review_is_refused_without_calling_the_fixer():
    fast = FakeLlm(script=[call("transfer_to_agent", agent_name="fix_pipeline")])
    coder = FakeLlm(script=["never"])
    s = ReviewSession(build_root_agent(Models(fast=fast, coder=coder)))
    try:
        out = await s.ask("Fix my code please")
        assert "nothing to fix yet" in out["answer"]
        assert coder.requests == [] and out["fix"] is None
    finally:
        await s.close()


async def test_exhausted_loop_reports_not_validated_and_marks_the_artifact(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_FIX_MAX_ITERATIONS", "2")
    agent, fast, coder = d.sample_agent(fixes=(d.FIX_ATTEMPT_1,))          # the same bad fix every time
    s, _ = await review(agent)
    try:
        out = await s.ask("fix it")
        assert out["state"][K.FIX_ITER] == 2 and out["state"][K.FIX_PASSED] is False
        assert out["fix"]["outcome"] == "not_validated"
        assert "NOT VALIDATED" in out["answer"] and "did NOT pass validation" in out["answer"]
        assert "fixed_code_UNVERIFIED.py" in out["artifacts"] and "fixed_code.py" not in out["artifacts"]
    finally:
        await s.close()


async def test_fixer_that_drops_a_public_function_is_rejected():
    dropped = "```python\ndef average(numbers):\n    \"\"\"x\"\"\"\n    return 0.0\n```"
    agent, fast, coder = d.sample_agent(fixes=(dropped, d.FIX_ATTEMPT_2))
    s, _ = await review(agent)
    try:
        out = await s.ask("fix it")
        assert out["state"][K.FIX_ITER] == 2 and out["state"][K.FIX_PASSED] and out["state"][K.FIX_RESULT]["problems"] == []
        prompt = next(coder.system_text(i) for i in range(len(coder.requests) - 1, -1, -1) if "ATTEMPT 1 WAS REJECTED" in coder.system_text(i))
        assert "Public names were removed or renamed: add_item, find_user" in prompt
    finally:
        await s.close()


async def test_fixer_reply_without_code_and_with_syntax_error_are_rejected_with_feedback():
    agent, fast, coder = d.sample_agent(fixes=("I would change the average function.", "```python\ndef average(:\n```", d.FIX_ATTEMPT_2))
    s, _ = await review(agent)
    try:
        out = await s.ask("fix it")
        assert out["state"][K.FIX_ITER] == 3 and out["state"][K.FIX_PASSED] is True
        prompts = [coder.system_text(i) for i in range(len(coder.requests)) if "TESTS THAT MUST PASS" in coder.system_text(i)]
        assert "contained no ```python block" in prompts[1]
        assert "syntax error at line" in prompts[2]
    finally:
        await s.close()


async def test_with_sandbox_off_the_fix_is_validated_statically_and_says_so(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_SANDBOX", "off")
    agent, fast, coder = d.sample_agent()
    s, rev = await review(agent)
    try:
        assert rev["state"][K.TEST_RESULT]["status"] == "skipped"
        assert "not run" in rev["report"]["test_assessment"].lower()
        out = await s.ask("fix it")
        res = out["state"][K.FIX_RESULT]
        assert out["state"][K.FIX_PASSED] and res["validated_by"] == "static" and "static checks only" in res["note"]
        assert "static checks only" in out["answer"]
        assert out["state"][K.FIX_ITER] == 2  # attempt 1 still had a hard-coded key: static rules rejected it
    finally:
        await s.close()


async def test_artifacts_are_written_to_disk_when_a_directory_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEREVIEW_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    agent, _, _ = d.sample_agent()
    s, out = await review(agent)
    try:
        files = [p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]
        assert any("review_report.json" in str(p) for p in files), files   # persisted on disk, not just in memory
        assert out["artifacts"]["review_report.md"] == 1
    finally:
        await s.close()
