"""A small offline eval set for the deterministic part of the pipeline (`tests/eval/cases/*.json`).

Each case is code plus what the tools must find, and optionally a pytest module plus the expected sandbox status.
This guards the things that must not regress whatever model is plugged in: which risks the analyzer sees, which style
rules fire, what the sandbox does with good, failing, hanging and dangerous code, and the verdict floor.

  python scripts/run_eval.py          # prints a table, exit code 1 on any failed expectation

Case format:
  {"name": ..., "code": ..., "tests": "..." (optional),
   "expect": {"syntax_ok": bool, "rules_present": [...], "rules_absent": [...], "style_rules": [...],
              "sandbox": "passed|failed|timeout|refused|...", "floor_verdict": "approve|approve_with_comments|request_changes",
              "min_complexity": {"fn": n}}}
The same JSON format can drive a live-model eval later by adding a "judge" section; those need an API key, so none ship here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import K
from .report import floor_verdict
from .tools.analyzer import analyze_code
from .tools.sandbox import run_pytest
from .tools.style import check_style_code

CASES_DIR = Path(__file__).resolve().parents[1] / "tests" / "eval" / "cases"


def load_cases(directory: Path = CASES_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def run_case(case: dict, sandbox_mode: str = "subprocess") -> list[str]:
    """Returns the list of failed expectations (empty = pass)."""
    exp, code = case["expect"], case["code"]
    analysis, style = analyze_code(code), check_style_code(code)
    problems: list[str] = []
    if "syntax_ok" in exp and (analysis["status"] == "ok") != exp["syntax_ok"]:
        problems.append(f"syntax_ok: expected {exp['syntax_ok']}, analyzer status {analysis['status']}")
    rules = {f["rule"] for f in analysis["findings"]}
    problems += [f"missing finding {r}" for r in exp.get("rules_present", []) if r not in rules]
    problems += [f"unexpected finding {r}" for r in exp.get("rules_absent", []) if r in rules]
    problems += [f"missing style rule {r}" for r in exp.get("style_rules", []) if r not in style["counts"]]
    funcs = {f["name"]: f for f in analysis["functions"]}
    for fn, minimum in (exp.get("min_complexity") or {}).items():
        if fn not in funcs or funcs[fn]["complexity"] < minimum:
            problems.append(f"complexity of {fn}: expected >= {minimum}, got {funcs.get(fn, {}).get('complexity')}")
    result: dict[str, Any] = {}
    if case.get("tests") is not None:
        result = run_pytest(code, case["tests"], mode=sandbox_mode, timeout=case.get("timeout", 5))
        if "sandbox" in exp and result["status"] != exp["sandbox"]:
            problems.append(f"sandbox: expected {exp['sandbox']}, got {result['status']} ({result.get('reason', '')[:80]})")
    if "floor_verdict" in exp:
        verdict, _ = floor_verdict({K.ANALYSIS: analysis, K.STYLE: style, K.TEST_RESULT: result})
        if verdict != exp["floor_verdict"]:
            problems.append(f"floor_verdict: expected {exp['floor_verdict']}, got {verdict}")
    return problems


def run_all(cases: list[dict] | None = None) -> dict[str, list[str]]:
    return {c["name"]: run_case(c) for c in (cases if cases is not None else load_cases())}
