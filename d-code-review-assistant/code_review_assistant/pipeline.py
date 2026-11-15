"""Builds the agent graph.

    code_review_assistant (LlmAgent, root: routes, never reviews)
      |- review_pipeline (SequentialAgent)
      |     CodeIntake -> code_analyzer -> style_checker -> test_runner -> feedback_synthesizer -> ReportRenderer
      |        (code)     (AST tool)      (style tool)     (LLM tests,     (structured JSON,       (policy floor,
      |                                                     sandbox)        Pydantic)               artifacts)
      '- fix_pipeline (SequentialAgent, guarded: needs a finished review)
            FixInit -> fix_loop (LoopAgent: code_fixer -> FixTestRunner -> FixValidator) -> fix_synthesizer -> FixRenderer

Stages talk only through session state (config.K). LLM stages never receive the code through a tool argument:
deterministic tools read it from state. Deterministic stages own every decision that is a fact (does it parse, do the
tests pass, may the loop exit, is the verdict allowed).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.genai import types

from . import config, models, prompts
from .config import K
from .report import findings_for_fixer
from .schemas import ReviewReport
from .stages import (CodeIntakeAgent, FixInitAgent, FixRendererAgent, FixTestRunnerAgent, FixValidatorAgent,
                     ReportRendererAgent)
from .tools.analyzer import analyze_code_structure
from .tools.style import check_code_style
from .tools.testing import run_generated_tests


@dataclass
class Models:
    """One model (name or BaseLlm instance) per role. `fast` = commentary/summaries, `coder` = tests/fix/review."""

    fast: Any = field(default_factory=lambda: models.make_model("fast"))
    coder: Any = field(default_factory=lambda: models.make_model("coder"))


def _val(state: Any, key: str, default: str = "(none)") -> str:
    v = state.get(key)
    if v is None or v == "":
        return default
    return v if isinstance(v, str) else json.dumps(v, default=str)


def _fill(template: str, **values: Callable[[Any], str]):
    """Instruction provider: one regex pass, so `{braces}` inside substituted code/JSON are never re-interpreted."""

    def provider(ctx: ReadonlyContext) -> str:
        return re.sub(r"\{(\w+)\}", lambda mo: values[mo[1]](ctx.state) if mo[1] in values else mo[0], template)

    return provider


def analysis_brief(state: Any) -> str:
    a = state.get(K.ANALYSIS)
    if not a:
        return "(not run)"
    brief = {"status": a.get("status"), "metrics": a.get("metrics"), "syntax_error": a.get("syntax_error"),
             "sensitive_imports": a.get("sensitive_imports"), "findings": a.get("findings"),
             "functions": [{"name": f["name"], "line": f["line"], "complexity": f["complexity"]}
                           for f in a.get("functions") or []]}
    return json.dumps(brief, default=str)[:6000]


def style_brief(state: Any) -> str:
    s = state.get(K.STYLE)
    return "(not run)" if not s else json.dumps({"issue_count": s["issue_count"], "counts": s["counts"],
                                                 "examples": s["issues"][:12]})[:3000]


def tests_brief(state: Any) -> str:
    t = state.get(K.TEST_RESULT)
    if not t:
        return "(not run)"
    keep = {k: t.get(k) for k in ("status", "passed", "failed", "errors", "failures", "reason", "sandbox", "duration_s")}
    return json.dumps(keep, default=str)[:3000]


def _require_intake(callback_context: Any) -> types.Content | None:
    """before_agent_callback of `review_core`: if intake rejected the submission, skip all review stages and say why."""
    intake = callback_context.state.get(K.INTAKE) or {}
    if intake.get("ok"):
        return None
    return types.Content(role="model", parts=[types.Part(text=intake.get("message") or "No code to review.")])


def build_review_pipeline(m: Models) -> SequentialAgent:
    gen = models.generation_config()
    code_analyzer = LlmAgent(
        name="code_analyzer", model=m.fast, description="Structure, complexity and risky constructs (AST tool).",
        instruction=prompts.CODE_ANALYZER, tools=[analyze_code_structure], output_key=K.STRUCTURE_NOTES,
        include_contents="none", generate_content_config=gen)
    style_checker = LlmAgent(
        name="style_checker", model=m.fast, description="PEP 8 / naming / docstring findings (rule tool).",
        instruction=_fill(prompts.STYLE_CHECKER, structure_notes=lambda s: _val(s, K.STRUCTURE_NOTES)),
        tools=[check_code_style], output_key=K.STYLE_NOTES, include_contents="none", generate_content_config=gen)
    test_runner = LlmAgent(
        name="test_runner", model=m.coder, description="Writes pytest tests and runs them in the sandbox.",
        instruction=_fill(prompts.TEST_RUNNER, code=lambda s: _val(s, K.CODE),
                          structure_notes=lambda s: _val(s, K.STRUCTURE_NOTES)),
        tools=[run_generated_tests], output_key=K.TEST_NOTES, include_contents="none", generate_content_config=gen)
    synth_kwargs: dict[str, Any] = {}
    if models.is_gemini() and config.strict_schema():  # opt-in: see config.strict_schema for why it is not the default
        synth_kwargs["output_schema"] = ReviewReport
    synthesizer = LlmAgent(
        name="feedback_synthesizer", model=m.coder, description="Combines all evidence into a structured review.",
        instruction=_fill(prompts.FEEDBACK_SYNTHESIZER, analysis=analysis_brief, style=style_brief, tests=tests_brief,
                          structure_notes=lambda s: _val(s, K.STRUCTURE_NOTES),
                          style_notes=lambda s: _val(s, K.STYLE_NOTES), test_notes=lambda s: _val(s, K.TEST_NOTES)),
        output_key=K.REPORT_RAW, include_contents="none", generate_content_config=gen, **synth_kwargs)
    core = SequentialAgent(
        name="review_core", description="The review stages that need accepted code.",
        before_agent_callback=_require_intake,
        sub_agents=[code_analyzer, style_checker, test_runner, synthesizer, ReportRendererAgent(name="report_renderer")])
    return SequentialAgent(
        name="review_pipeline",
        description="Reviews pasted Python code: structure, style, sandboxed tests, then a structured report.",
        sub_agents=[CodeIntakeAgent(name="code_intake"), core])


def _require_review(callback_context: Any) -> types.Content | None:
    """before_agent_callback of the fix pipeline: refuse to 'fix' code nobody has reviewed."""
    state = callback_context.state
    if not state.get(K.CODE) or not state.get(K.REPORT):
        return types.Content(role="model", parts=[types.Part(
            text="There is nothing to fix yet: no code has been reviewed in this session. Paste the code first.")])
    return None


def build_fix_pipeline(m: Models) -> SequentialAgent:
    gen = models.generation_config()
    fixer = LlmAgent(
        name="code_fixer", model=m.coder, description="Rewrites the module to resolve the review findings.",
        instruction=_fill(prompts.CODE_FIXER, code=lambda s: _val(s, K.CODE), findings=lambda s: findings_for_fixer(s),
                          tests=lambda s: _val(s, K.TESTS, "(no tests were generated)"),
                          feedback=lambda s: _val(s, K.FIX_FEEDBACK, "")),
        output_key=K.FIXER_OUT, include_contents="none", generate_content_config=gen)
    loop = LoopAgent(
        name="fix_loop", description="Fix, validate in the sandbox, repeat until validated or out of attempts.",
        max_iterations=config.fix_max_iterations(),
        sub_agents=[fixer, FixTestRunnerAgent(name="fix_test_runner"), FixValidatorAgent(name="fix_validator")])

    def outcome(s: Any) -> str:
        return "validated" if s.get(K.FIX_PASSED) else "NOT validated"

    def validation(s: Any) -> str:
        r = s.get(K.FIX_RESULT) or {}
        return json.dumps({"validated_by": r.get("validated_by"), "status": r.get("status"),
                           "problems": r.get("problems"), "note": r.get("note")}, default=str)

    synth = LlmAgent(
        name="fix_synthesizer", model=m.fast, description="Explains what the fix changed and how far to trust it.",
        instruction=_fill(prompts.FIX_SYNTHESIZER, outcome=outcome, validation=validation,
                          attempts=lambda s: str(s.get(K.FIX_ITER) or 0), findings=lambda s: findings_for_fixer(s),
                          fixer_text=lambda s: _val(s, K.FIXER_OUT)[-2500:]),
        output_key=K.FIX_NOTES, include_contents="none", generate_content_config=gen)
    return SequentialAgent(
        name="fix_pipeline", description="Fixes code that was reviewed in this session and validates the fix.",
        before_agent_callback=_require_review,
        sub_agents=[FixInitAgent(name="fix_init"), loop, synth, FixRendererAgent(name="fix_renderer")])


def build_root_agent(m: Models | None = None) -> LlmAgent:
    m = m or Models()
    return LlmAgent(
        name="code_review_assistant", model=m.fast,
        description="Reviews Python code (structure, risks, style, sandboxed tests) and can validate a fix.",
        instruction=prompts.ROOT, sub_agents=[build_review_pipeline(m), build_fix_pipeline(m)],
        generate_content_config=models.generation_config())
