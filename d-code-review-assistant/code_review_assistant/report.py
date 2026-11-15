"""Deterministic report logic: a fallback report, the policy floor applied to the model's report, rendering.

The LLM writes the prose; code decides what it may not get wrong. A review that omits a critical finding the AST
analyzer saw, or approves code whose tests fail, is corrected here, and the correction is recorded in
`report["policy_notes"]` so it is visible rather than silent.
"""

from __future__ import annotations

from typing import Any, Mapping

from .config import K
from .schemas import SEVERITY_ORDER, Finding, ReviewReport, Verdict

VERDICT_RANK: dict[str, int] = {"approve": 0, "approve_with_comments": 1, "request_changes": 2}
VERDICT_LABEL = {"approve": "APPROVE", "approve_with_comments": "APPROVE WITH COMMENTS", "request_changes": "REQUEST CHANGES"}
SECURITY_RULES = ("risky-call", "hardcoded-secret", "sql-injection")


def _category(rule: str) -> str:
    if rule.startswith(SECURITY_RULES):
        return "security"
    if rule in ("mutable-default-arg", "bare-except", "swallowed-exception"):
        return "correctness"
    return "maintainability"


def analyzer_findings(state: Mapping[str, Any], min_severity: str = "info") -> list[Finding]:
    limit = SEVERITY_ORDER[min_severity]
    out = []
    analysis = state.get(K.ANALYSIS) or {}
    if analysis.get("status") == "syntax_error":
        e = analysis.get("syntax_error") or {}
        out.append(Finding(category="correctness", severity="critical", line=e.get("line"),
                           title="Syntax error", detail=str(e.get("message", "")),
                           suggestion="The module cannot be imported until this is fixed."))
    for f in analysis.get("findings") or []:
        if SEVERITY_ORDER.get(f["severity"], 4) <= limit:
            out.append(Finding(category=_category(f["rule"]), severity=f["severity"], line=f["line"],
                               title=f["message"], detail=f"rule: {f['rule']}"))
    return sorted(out, key=lambda x: (SEVERITY_ORDER[x.severity], x.line or 0))


def floor_verdict(state: Mapping[str, Any]) -> tuple[Verdict, list[str]]:
    """The mildest verdict the evidence allows, and the reasons it is not lower."""
    reasons: list[str] = []
    analysis, tests, style = state.get(K.ANALYSIS) or {}, state.get(K.TEST_RESULT) or {}, state.get(K.STYLE) or {}
    if analysis.get("status") == "syntax_error":
        reasons.append("the code does not parse")
    serious = [f for f in analysis.get("findings") or [] if f["severity"] in ("critical", "high")]
    if serious:
        reasons.append(f"{len(serious)} critical/high finding(s) from the analyzer")
    if tests.get("status") in ("failed", "timeout"):
        reasons.append(f"the executed tests did not pass ({tests['status']})")
    if reasons:
        return "request_changes", reasons
    soft = []
    if any(f["severity"] == "medium" for f in analysis.get("findings") or []):
        soft.append("medium-severity findings")
    if style.get("issue_count"):
        soft.append("style issues")
    if tests.get("status") in ("refused", "skipped", "invalid_tests", "error", "no_tests", None):
        soft.append("tests did not validate the code")
    if soft:
        return "approve_with_comments", soft
    return "approve", []


def fallback_report(state: Mapping[str, Any]) -> ReviewReport:
    """Built from tool results alone: used when the model's JSON cannot be parsed."""
    verdict, reasons = floor_verdict(state)
    tests, style = state.get(K.TEST_RESULT) or {}, state.get(K.STYLE) or {}
    findings = analyzer_findings(state)
    for f in (tests.get("failures") or [])[:5]:
        findings.append(Finding(category="testing", severity="high", title=f"Failing test: {f}",
                                suggestion="Check whether the code or the test is wrong."))
    if style.get("issue_count"):
        top = ", ".join(f"{r} x{n}" for r, n in sorted(style["counts"].items(), key=lambda kv: -kv[1])[:4])
        findings.append(Finding(category="style", severity="low", title=f"{style['issue_count']} style issues ({top})"))
    return ReviewReport(
        verdict=verdict,
        summary="Automatic summary built from the deterministic checks only (the reviewer model's output was not "
                "usable). " + ("Reasons: " + "; ".join(reasons) + "." if reasons else "No blocking evidence found."),
        findings=sorted(findings, key=lambda x: (SEVERITY_ORDER[x.severity], x.line or 0)),
        test_assessment=_test_line(tests),
        next_steps=["Address the critical and high findings first.", "Re-run the review after the fix."]
        if findings else [],
    )


def _test_line(t: Mapping[str, Any]) -> str:
    if not t:
        return "No tests were run."
    status = t.get("status")
    if status in ("passed", "failed", "error", "no_tests"):
        return (f"{t.get('passed', 0)} passed, {t.get('failed', 0)} failed, {t.get('errors', 0)} errors "
                f"(sandbox: {t.get('sandbox')}, {t.get('duration_s', '?')}s).")
    return f"Tests were not run: {status}. {t.get('reason', '')}".strip()


def apply_policy(report: ReviewReport, state: Mapping[str, Any]) -> tuple[ReviewReport, list[str]]:
    """Raise the verdict to the evidence floor and re-add critical/high analyzer findings the model dropped."""
    notes: list[str] = []
    floor, reasons = floor_verdict(state)
    if VERDICT_RANK[report.verdict] < VERDICT_RANK[floor]:
        notes.append(f"verdict raised from {report.verdict} to {floor}: " + "; ".join(reasons))
        report.verdict = floor
    seen_lines = {f.line for f in report.findings if f.line is not None}
    for f in analyzer_findings(state, min_severity="high"):
        if f.line not in seen_lines:
            report.findings.append(f)
            notes.append(f"added missing {f.severity} finding at line {f.line}: {f.title}")
    report.findings.sort(key=lambda x: (SEVERITY_ORDER[x.severity], x.line or 0))
    tests = state.get(K.TEST_RESULT) or {}
    if tests.get("status") not in ("passed", "failed", "error", "no_tests", "timeout"):
        # nothing was executed, so the model's claims about test results are unsupported: state the fact instead
        if report.test_assessment.strip():
            notes.append("test assessment replaced: the tests did not run, so the model's claims about them are unsupported")
        report.test_assessment = _test_line(tests)
    return report, notes


def render_markdown(report: ReviewReport, state: Mapping[str, Any], round_no: int = 1, notes: list[str] | None = None) -> str:
    a = state.get(K.ANALYSIS) or {}
    m = a.get("metrics") or {}
    lines = [f"# Code review (round {round_no}): {VERDICT_LABEL[report.verdict]}", "", report.summary, ""]
    if m:
        lines += [f"**Size** {m.get('lines', '?')} lines, {m.get('functions', 0)} functions, {m.get('classes', 0)} classes, "
                  f"max complexity {m.get('max_complexity', 0)}.", ""]
    if report.findings:
        lines += ["## Findings", "", "| # | Severity | Category | Line | Finding |", "|---|---|---|---|---|"]
        for i, f in enumerate(report.findings, 1):
            text = f.title + (f" - {f.suggestion}" if f.suggestion else "")
            lines.append(f"| {i} | {f.severity} | {f.category} | {f.line or '-'} | {text.replace('|', '/')} |")
        lines.append("")
    if report.test_assessment:
        lines += ["## Tests", "", report.test_assessment, ""]
    if report.strengths:
        lines += ["## Strengths", ""] + [f"- {s}" for s in report.strengths] + [""]
    if report.next_steps:
        lines += ["## Next steps", ""] + [f"- {s}" for s in report.next_steps] + [""]
    if notes:
        lines += ["## Policy notes (applied by code, not by the model)", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(lines).rstrip() + "\n"


def findings_for_fixer(state: Mapping[str, Any], limit: int = 12) -> str:
    """The reviewed findings as a plain list for the fixer prompt (from the validated report)."""
    report = state.get(K.REPORT) or {}
    items = report.get("findings") or []
    if not items:
        return "(none recorded)"
    return "\n".join(f"- [{f['severity']}] line {f.get('line') or '?'}: {f['title']}"
                     + (f" -> {f['suggestion']}" if f.get("suggestion") else "") for f in items[:limit])
