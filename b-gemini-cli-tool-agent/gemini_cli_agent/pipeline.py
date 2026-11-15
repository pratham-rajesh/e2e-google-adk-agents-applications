"""Builds the agent graph. `Models` lets tests swap in scripted fake models per role."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.readonly_context import ReadonlyContext

from . import models, prompts
from .config import K
from .stages import RequestCaptureAgent, RunRecorderAgent
from .tools.audit import calls_summary
from .tools.gemini_cli import gemini_cli
from .tools.workspace import clone_repository, list_workspace


@dataclass
class Models:
    """One model (name or BaseLlm instance) per role."""

    planner: Any = field(default_factory=lambda: models.make_model("fast"))
    worker: Any = field(default_factory=lambda: models.make_model("fast"))
    reviewer: Any = field(default_factory=lambda: models.make_model("smart"))


def _fill(template: str, **values: str):
    """Instruction provider: substitutes values itself so `{braces}` in state text stay literal."""

    def provider(ctx: ReadonlyContext) -> str:
        # Single pass: substituted text is never re-scanned, so a request containing "{plan}" stays literal.
        return re.sub(r"\{(\w+)\}", lambda mo: values[mo[1]](ctx.state) if mo[1] in values else mo[0], template)

    return provider


def build_pipeline(m: Models | None = None) -> SequentialAgent:
    m = m or Models()
    gen_cfg = models.generation_config()

    planner = LlmAgent(
        name="TaskPlanner",
        model=m.planner,
        description="Turns the request into a precise, read-only brief for Gemini CLI.",
        instruction=_fill(prompts.PLANNER),
        output_key=K.PLAN,
        include_contents="none",  # sees the request via the instruction below, not chat history
        generate_content_config=gen_cfg,
    )
    # The planner has no {placeholders}, so give it the request explicitly.
    planner.instruction = _fill(
        prompts.PLANNER + "\nUSER REQUEST\n{request}\n",
        request=lambda s: str(s.get(K.REQUEST, "")),
    )

    worker = LlmAgent(
        name="GeminiCliWorker",
        model=m.worker,
        description="Clones the repo if needed and runs Gemini CLI via the gemini_cli tool.",
        instruction=_fill(prompts.WORKER, plan=lambda s: str(s.get(K.PLAN, ""))),
        tools=[clone_repository, list_workspace, gemini_cli],
        output_key=K.RESULT,
        include_contents="none",
        generate_content_config=gen_cfg,
    )

    reviewer = LlmAgent(
        name="ResultReviewer",
        model=m.reviewer,
        description="Checks the result against the request, the plan and the CLI call log.",
        instruction=_fill(
            prompts.REVIEWER,
            request=lambda s: str(s.get(K.REQUEST, "")),
            plan=lambda s: str(s.get(K.PLAN, "")),
            calls=lambda s: calls_summary(s.get(K.CLI_CALLS) or []),
            result=lambda s: str(s.get(K.RESULT, "")),
        ),
        output_key=K.REVIEW,
        include_contents="none",
        generate_content_config=gen_cfg,
    )

    return SequentialAgent(
        name="GeminiCliPipeline",
        description="Plans a task, runs it through Gemini CLI as a tool, then reviews the result.",
        sub_agents=[
            RequestCaptureAgent(name="RequestCapture"),
            planner,
            worker,
            reviewer,
            RunRecorderAgent(name="RunRecorder"),
        ],
    )


def build_root_agent(m: Models | None = None) -> SequentialAgent:
    return build_pipeline(m)
