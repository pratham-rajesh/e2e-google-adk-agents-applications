"""Lenient parsing of the model's JSON, and the evidence policy that code applies on top of the model's review."""

import json

import pytest

from code_review_assistant import report as rep
from code_review_assistant.config import K
from code_review_assistant.schemas import Finding, ReviewReport, parse_report
from code_review_assistant.tools.analyzer import analyze_code
from code_review_assistant.tools.style import check_style_code

GOOD = {"verdict": "approve", "summary": "ok", "findings": []}


def state_for(code: str, tests: dict | None = None) -> dict:
    return {K.ANALYSIS: analyze_code(code), K.STYLE: check_style_code(code), K.TEST_RESULT: tests}


# ---- parse_report ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    GOOD,
    json.dumps(GOOD),
    "```json\n" + json.dumps(GOOD) + "\n```",
    "Here is my review:\n" + json.dumps(GOOD) + "\nHope that helps!",
    ReviewReport(**GOOD),
])
def test_parse_accepts_dict_text_fences_and_prose(raw):
    assert parse_report(raw).verdict == "approve"


def test_parse_normalises_loose_vocabulary():
    r = parse_report({"verdict": "Request Changes", "summary": "s",
                      "findings": [{"category": "x", "severity": "Warning", "title": "t"},
                                   {"category": "x", "severity": "BLOCKER", "title": "t2", "line": 3}]})
    assert r.verdict == "request_changes" and [f.severity for f in r.findings] == ["medium", "critical"]
    assert parse_report({"verdict": "approved", "summary": "s"}).verdict == "approve"
    assert parse_report({"verdict": "changes-requested", "summary": "s"}).verdict == "request_changes"


def test_parse_handles_braces_inside_strings():
    text = 'noise {"verdict": "approve", "summary": "use {x} and } in text", "findings": []} tail {oops'
    assert parse_report(text).summary == "use {x} and } in text"


@pytest.mark.parametrize("raw", [None, "", "no json here", "{not json}", '{"verdict": "maybe", "summary": "s"}',
                                 '{"summary": "missing verdict"}', [1, 2], 42])
def test_parse_rejects_garbage(raw):
    assert parse_report(raw) is None


# ---- floor verdict & policy -----------------------------------------------------------------------------------
def test_floor_verdict_levels():
    clean = 'def f():\n    """x"""\n    return 1\n'
    passed = {"status": "passed"}
    assert rep.floor_verdict(state_for(clean, passed)) == ("approve", [])
    assert rep.floor_verdict(state_for(clean, None))[0] == "approve_with_comments"        # tests never ran
    assert rep.floor_verdict(state_for(clean, {"status": "refused"}))[0] == "approve_with_comments"
    assert rep.floor_verdict(state_for("import os\n", passed))[0] == "approve_with_comments"  # style only
    assert rep.floor_verdict(state_for(clean, {"status": "failed"}))[0] == "request_changes"
    assert rep.floor_verdict(state_for(clean, {"status": "timeout"}))[0] == "request_changes"
    assert rep.floor_verdict(state_for("eval(x)", passed))[0] == "request_changes"
    assert rep.floor_verdict(state_for("def f(:\n", passed))[0] == "request_changes"
    # a collection error in a *generated* test is doubtful evidence: it must not block on its own
    assert rep.floor_verdict(state_for(clean, {"status": "error"}))[0] == "approve_with_comments"


def test_policy_raises_a_too_lenient_verdict_and_readds_dropped_findings():
    state = state_for('API_KEY = "abcd1234"\n\n\ndef f(x):\n    """x"""\n    return eval(x)\n', {"status": "passed", "passed": 2})
    lenient = ReviewReport(verdict="approve", summary="fine", findings=[Finding(category="style", severity="low", title="nit")])
    fixed, notes = rep.apply_policy(lenient, state)
    assert fixed.verdict == "request_changes"
    assert {f.line for f in fixed.findings if f.severity in ("critical", "high")} == {1, 6}
    assert any("raised" in n for n in notes) and sum("added missing" in n for n in notes) == 2
    assert [f.severity for f in fixed.findings] == sorted((f.severity for f in fixed.findings),
                                                          key=lambda s: ["critical", "high", "medium", "low", "info"].index(s))


def test_policy_never_lowers_a_stricter_model_verdict_or_duplicates_findings():
    state = state_for('def f():\n    """x"""\n    return 1\n', {"status": "passed", "passed": 1})
    strict = ReviewReport(verdict="request_changes", summary="s", findings=[])
    out, notes = rep.apply_policy(strict, state)
    assert out.verdict == "request_changes" and notes == []
    state = state_for("eval(x)\n", {"status": "passed"})
    r = ReviewReport(verdict="request_changes", summary="s",
                     findings=[Finding(category="security", severity="critical", line=1, title="eval")])
    out, notes = rep.apply_policy(r, state)
    assert len(out.findings) == 1 and not any("added" in n for n in notes)


def test_policy_replaces_test_claims_when_nothing_ran():
    state = state_for('def f():\n    """x"""\n    return 1\n', {"status": "refused", "reason": "imports socket"})
    r = ReviewReport(verdict="approve", summary="s", test_assessment="All tests pass, great coverage.")
    out, notes = rep.apply_policy(r, state)
    assert "pass" not in out.test_assessment.lower().replace("not run", "") and "refused" in out.test_assessment
    assert any("test assessment replaced" in n for n in notes)
    # but claims about tests that DID run are left alone
    state = state_for('def f():\n    """x"""\n    return 1\n', {"status": "passed"})
    out, _ = rep.apply_policy(ReviewReport(verdict="approve", summary="s", test_assessment="Solid."), state)
    assert out.test_assessment == "Solid."


def test_fallback_report_is_built_from_tool_results():
    state = state_for('API_KEY = "abcd1234"\nimport os\n', {"status": "failed", "failures": ["FAILED t::a - boom"], "passed": 0, "failed": 1})
    r = rep.fallback_report(state)
    assert r.verdict == "request_changes" and "not usable" in r.summary
    cats = {f.category for f in r.findings}
    assert {"security", "testing", "style"} <= cats
    assert "0 passed, 1 failed" in r.test_assessment


def test_render_markdown_contains_the_essentials():
    state = state_for("eval(x)\n", {"status": "passed", "passed": 1, "failed": 0, "errors": 0, "sandbox": "subprocess"})
    r, notes = rep.apply_policy(ReviewReport(verdict="approve", summary="Looks fine.", strengths=["short"]), state)
    md = rep.render_markdown(r, state, round_no=2, notes=notes)
    assert md.startswith("# Code review (round 2): REQUEST CHANGES")
    assert "| critical | security | 1 |" in md and "## Policy notes" in md and "- short" in md


def test_findings_for_fixer_formats_the_validated_report():
    assert rep.findings_for_fixer({}) == "(none recorded)"
    state = {K.REPORT: {"findings": [{"severity": "high", "line": 3, "title": "Hard-coded key", "suggestion": "use env"},
                                     {"severity": "low", "line": None, "title": "nit"}]}}
    out = rep.findings_for_fixer(state)
    assert "- [high] line 3: Hard-coded key -> use env" in out and "- [low] line ?: nit" in out
