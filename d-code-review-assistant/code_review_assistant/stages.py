"""Deterministic pipeline stages (plain code, no LLM).

  CodeIntakeAgent     extracts the code from the user's message into state, validates size/syntax, resets run state
  ReportRendererAgent validates the synthesizer's JSON, applies the evidence policy, renders + saves the report
  FixInitAgent        resets fix-loop state
  FixTestRunnerAgent  extracts the candidate fix, runs the review's tests against it in the sandbox
  FixValidatorAgent   ends the loop (escalate) when the candidate is validated
  FixRendererAgent    the final fix message: outcome, diff, code; saves versioned artifacts
"""

from __future__ import annotations

import ast
import asyncio
import difflib
import json
import re
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.context import Context
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from . import config, report as rep
from .config import K
from .schemas import parse_report
from .tools import sandbox
from .tools.analyzer import analyze_code, public_api

FENCE = re.compile(r"```[ \t]*(?:python|py|python3)?[ \t]*\r?\n(.*?)```", re.S | re.I)
CODE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Assign,
              ast.For, ast.While, ast.If, ast.With, ast.Try)


def say(author: str, text: str | None = None, actions: EventActions | None = None, **state_delta: Any) -> Event:
    content = types.Content(role="model", parts=[types.Part(text=text)]) if text else None
    actions = actions or EventActions()
    actions.state_delta.update(state_delta)
    return Event(author=author, content=content, actions=actions)


def text_of(content: types.Content | None) -> str:
    return "\n".join(p.text for p in (content.parts if content else None) or [] if p.text).strip()


def extract_code(text: str) -> str | None:
    """Python source from a chat message: the first fenced block, else the whole message if it looks like code."""
    blocks = [b.strip("\n") for b in FENCE.findall(text)]
    blocks = [b for b in blocks if b.strip()]
    if blocks:
        return max(blocks, key=len) if len(blocks) > 1 and not text.strip().startswith("```") else blocks[0]
    body = text.strip("\n")
    if not body.strip():
        return None
    try:
        tree = ast.parse(body)
    except SyntaxError:
        # unparsable, but if it reads like Python the reviewer should report the syntax error
        return body if re.search(r"^\s*(def|class|import|from)\s+\w", body, re.M) else None
    return body if any(isinstance(n, CODE_NODES) for n in tree.body) else None


def _s(state: Any, key: str, default: Any = "") -> Any:
    v = state.get(key)
    return default if v is None else v


class CodeIntakeAgent(BaseAgent):
    """First stage of the review: the code goes into state once, so tools and prompts read it from there."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        text = text_of(ctx.user_content)
        code = extract_code(text)
        limit = config.max_code_chars()
        # A rejected submission is recorded in state; the guard on `review_core` (pipeline.py) then skips every later
        # stage and shows `message`. (ADK's SequentialAgent does not stop when a child sets ctx.end_invocation.)
        if code is None:
            yield say(self.name, None, **{K.INTAKE: {"ok": False, "message": "I could not find Python code in your "
                      "message. Paste the code (ideally in a ```python fenced block) and I will review it."}})
            return
        if len(code) > limit:
            yield say(self.name, None, **{K.INTAKE: {"ok": False, "message": f"That submission is {len(code):,} "
                      f"characters; the limit is {limit:,}. Send a smaller module or one file at a time."}})
            return
        syntax_error = None
        try:
            ast.parse(code)
        except SyntaxError as exc:
            syntax_error = {"line": exc.lineno, "message": exc.msg}
        delta: dict[str, Any] = {k: None for k in K.REVIEW_KEYS + K.FIX_KEYS}
        delta.update({
            K.CODE: code,
            K.INTAKE: {"ok": True, "chars": len(code), "lines": len(code.splitlines()),
                       "syntax_ok": syntax_error is None, "syntax_error": syntax_error},
            K.ROUND: int(ctx.session.state.get(K.ROUND) or 0) + 1,
        })
        note = f"Received {len(code.splitlines())} lines of Python"
        note += f" (syntax error at line {syntax_error['line']}: {syntax_error['message']})." if syntax_error else "."
        yield say(self.name, note, **delta)


async def _save(ctx: InvocationContext, actions: EventActions, filename: str, text: str, mime: str = "text/plain") -> int | None:
    """Save a versioned artifact; the version lands in the event's artifact_delta. No-op without a service."""
    try:
        return await Context(ctx, event_actions=actions).save_artifact(
            filename, types.Part(inline_data=types.Blob(data=text.encode("utf-8"), mime_type=mime)))
    except ValueError:
        return None


class ReportRendererAgent(BaseAgent):
    """Turns the synthesizer's output into the validated report, enforces the evidence floor, saves artifacts."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        parsed = parse_report(state.get(K.REPORT_RAW))
        source = "model" if parsed else "fallback"
        report, notes = rep.apply_policy(parsed or rep.fallback_report(state), state)
        round_no = int(state.get(K.ROUND) or 1)
        markdown = rep.render_markdown(report, state, round_no, notes)
        record = {**report.model_dump(), "source": source, "policy_notes": notes, "round": round_no}
        actions = EventActions()
        v_json = await _save(ctx, actions, "review_report.json", json.dumps(record, indent=2), "application/json")
        v_md = await _save(ctx, actions, "review_report.md", markdown, "text/markdown")
        if v_json is not None:
            markdown += f"\n_Saved as artifacts review_report.json (version {v_json}) and review_report.md (version {v_md})._\n"
        yield say(self.name, markdown, actions, **{K.REPORT: record})


class FixInitAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        yield say(self.name, None, **{**{k: None for k in K.FIX_KEYS}, K.FIX_ITER: 0, K.FIX_PASSED: False})


def _feedback_for_failure(result: dict) -> str:
    tests = result.get("tests") or {}
    parts = [f"ATTEMPT {result['iteration']} WAS REJECTED."]
    if result.get("problems"):
        parts += [f"- {p}" for p in result["problems"]]
    if tests.get("failures"):
        parts += ["Failing tests:"] + [f"- {f}" for f in tests["failures"][:6]]
    if tests.get("status") in ("failed", "error", "timeout") and tests.get("output"):
        parts += ["Test output (tail):", tests["output"][-1500:]]
    return "\n".join(parts) + "\nFix these and return the complete module again.\n"


class FixTestRunnerAgent(BaseAgent):
    """Validates the fixer's candidate: static checks, then the review's pytest module in the sandbox."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        it = int(state.get(K.FIX_ITER) or 0) + 1
        original, tests = state.get(K.CODE) or "", state.get(K.TESTS) or ""
        candidate = extract_code(str(state.get(K.FIXER_OUT) or ""))
        result: dict[str, Any] = {"iteration": it, "status": "no_candidate", "validated_by": None, "passed": False,
                                  "problems": [], "tests": None}
        if candidate is None:
            result["problems"].append("Your reply contained no ```python block with the complete module.")
        else:
            analysis = analyze_code(candidate)
            if analysis["status"] == "syntax_error":
                e = analysis["syntax_error"]
                result.update(status="syntax_error")
                result["problems"].append(f"The module has a syntax error at line {e['line']}: {e['message']}.")
            else:
                removed = sorted(public_api(original) - public_api(candidate))
                if removed:
                    result["problems"].append(f"Public names were removed or renamed: {', '.join(removed)}. Keep them.")
                serious = [f for f in analysis["findings"] if f["severity"] in ("critical", "high")]
                for f in serious:
                    result["problems"].append(f"Still present: {f['message']} (line {f['line']}).")
                run = await asyncio.to_thread(sandbox.run_pytest, candidate, tests) if tests else \
                    {"status": "invalid_tests", "reason": "no tests were generated during the review"}
                result["tests"] = sandbox.summarize_for_llm(run)
                if run["status"] in ("passed", "failed", "timeout"):
                    result["validated_by"] = "tests"
                    result["status"] = run["status"]
                    tests_ok = run["status"] == "passed"
                else:  # tests could not give a verdict (refused / invalid / skipped / error): fall back to static checks
                    result["validated_by"] = "static"
                    result["status"] = "static_only"
                    tests_ok = True
                    result["note"] = f"tests could not run ({run.get('reason') or run['status']}); static checks only"
                result["passed"] = bool(tests_ok and not removed and not serious)
        feedback = "" if result["passed"] else _feedback_for_failure(result)
        t = result.get("tests") or {}
        line = (f"Fix attempt {it}: " + (f"{t.get('passed', 0)} passed, {t.get('failed', 0)} failed" if result["validated_by"] == "tests"
                else "no test verdict" if candidate else "no usable code")
                + (" - VALIDATED" if result["passed"] else " - rejected"))
        delta: dict[str, Any] = {K.FIX_RESULT: result, K.FIX_FEEDBACK: feedback, K.FIX_ITER: it, K.FIX_PASSED: result["passed"]}
        if candidate is not None:
            delta[K.FIXED_CODE] = candidate
        yield say(self.name, line, **delta)


class FixValidatorAgent(BaseAgent):
    """Exit condition of the fix loop. Deterministic on purpose: 'did the tests pass' is a fact, not a judgement."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        passed = bool(ctx.session.state.get(K.FIX_PASSED))
        it = ctx.session.state.get(K.FIX_ITER)
        yield say(self.name, "Fix validated: leaving the loop." if passed
                  else f"Attempt {it} rejected: another round, up to the iteration limit.",
                  actions=EventActions(escalate=passed))


def unified_diff(old: str, new: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "original.py", "fixed.py"))


class FixRendererAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        original, fixed = state.get(K.CODE) or "", state.get(K.FIXED_CODE)
        passed = bool(state.get(K.FIX_PASSED))
        res = state.get(K.FIX_RESULT) or {}
        notes = str(state.get(K.FIX_NOTES) or "").strip()
        outcome = "validated" if passed else ("not_validated" if fixed else "no_fix")
        title = {"validated": "VALIDATED", "not_validated": "NOT VALIDATED", "no_fix": "NO FIX PRODUCED"}[outcome]
        how = {"tests": "the review's tests pass in the sandbox", "static": "tests could not run; static checks only"}.get(
            res.get("validated_by") or "", "no validation verdict")
        lines = [f"# Fix result: {title}", "", notes or "(no summary)", "",
                 f"**Attempts:** {state.get(K.FIX_ITER) or 0} of {config.fix_max_iterations()}.  **Validation:** {how}."]
        if outcome == "not_validated":
            lines += ["", "> The candidate below did NOT pass validation. Treat it as a draft, not a fix.",
                      *(f"> - {p}" for p in (res.get("problems") or [])[:5])]
        actions = EventActions()
        record = {"outcome": outcome, "attempts": state.get(K.FIX_ITER) or 0, "validated_by": res.get("validated_by"),
                  "problems": res.get("problems") or [], "diff_lines": 0}
        if fixed:
            diff = unified_diff(original, fixed)
            record["diff_lines"] = len(diff.splitlines())
            lines += ["", "## Diff", "```diff", diff.rstrip() or "(no changes)", "```", "", "## Fixed code",
                      "```python", fixed.rstrip(), "```"]
            name = "fixed_code.py" if passed else "fixed_code_UNVERIFIED.py"
            v = await _save(ctx, actions, name, fixed, "text/x-python")
            if v is not None:
                lines += ["", f"_Saved as artifact {name} (version {v})._"]
        text = "\n".join(lines) + "\n"
        await _save(ctx, actions, "fix_report.md", text, "text/markdown")
        yield say(self.name, text, actions, **{K.FIX_REPORT: record})
