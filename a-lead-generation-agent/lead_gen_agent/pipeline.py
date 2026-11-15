"""Builds the agent graph. `models` lets tests swap in scripted fake models per role."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import Agent, LlmAgent, LoopAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import exit_loop
from google.genai import types

from . import config, models, prompts
from .config import K
from .schemas import LeadBatch
from .stages import (
    EnrichmentAgent,
    InitResearchAgent,
    LeadMergerAgent,
    ScoringAgent,
    SignalResearchAgent,
)
from .tools.brief import approve_research_brief, save_research_brief
from .tools.search import search_tools


@dataclass
class Models:
    """One model (name or BaseLlm instance) per role."""

    coordinator: Any = field(default_factory=lambda: models.make_model("fast"))
    researcher: Any = field(default_factory=lambda: models.make_model("fast"))
    extractor: Any = field(default_factory=lambda: models.make_model("fast"))
    evaluator: Any = field(default_factory=lambda: models.make_model("fast"))
    signals: Any = field(default_factory=lambda: models.make_model("fast"))
    composer: Any = field(default_factory=lambda: models.make_model("smart"))


def _require_approved_brief(callback_context: CallbackContext) -> types.Content | None:
    """Guardrail: the expensive pipeline must never run without a user-approved brief."""
    state = callback_context.state
    if state.get(K.BRIEF) and state.get(K.APPROVED):
        return None
    return types.Content(
        role="model",
        parts=[types.Part(text="I can't start the research yet: the brief hasn't been approved.")],
    )


def build_pipeline(m: Models | None = None) -> SequentialAgent:
    m = m or Models()

    discovery_loop = LoopAgent(
        name="DiscoveryLoop",
        max_iterations=config.max_iterations(),
        description="Iteratively searches the web until the lead pool is large and diverse enough.",
        sub_agents=[
            # Search tools can't be combined with output_schema, hence the researcher -> extractor
            # split: free-text research first, structured extraction second.
            LlmAgent(
                name="DiscoveryResearcher",
                model=m.researcher,
                instruction=prompts.DISCOVERY_RESEARCHER,
                tools=search_tools(),
                output_key=K.RAW_FINDINGS,
                generate_content_config=models.generation_config(),
            ),
            LlmAgent(
                name="LeadExtractor",
                model=m.extractor,
                # Small local models are unreliable with constrained decoding, so only Gemini gets a
                # hard output_schema; others are told to emit JSON and LeadMerger parses it leniently.
                instruction=prompts.LEAD_EXTRACTOR + ("" if models.is_gemini() else prompts.JSON_ONLY_SUFFIX),
                output_schema=LeadBatch if models.is_gemini() else None,
                output_key=K.BATCH,
                include_contents="none",
                generate_content_config=models.generation_config(),
            ),
            LeadMergerAgent(name="LeadMerger"),
            LlmAgent(
                name="CoverageEvaluator",
                model=m.evaluator,
                instruction=prompts.COVERAGE_EVALUATOR,
                tools=[exit_loop],
                output_key=K.GAPS,
                include_contents="none",
                generate_content_config=models.generation_config(),
            ),
        ],
    )

    return SequentialAgent(
        name="LeadResearchPipeline",
        description="Runs approved lead research end to end: discover, enrich, analyse, score, report.",
        before_agent_callback=_require_approved_brief,
        sub_agents=[
            InitResearchAgent(name="InitResearch"),
            discovery_loop,
            EnrichmentAgent(name="Enrichment"),
            SignalResearchAgent(name="SignalResearch", model=m.signals),
            ScoringAgent(name="Scoring"),
            LlmAgent(
                name="ReportComposer",
                model=m.composer,
                instruction=prompts.REPORT_COMPOSER,
                output_key=K.REPORT,
                include_contents="none",
                generate_content_config=models.generation_config(),
            ),
        ],
    )


def build_root_agent(m: Models | None = None) -> Agent:
    m = m or Models()
    return Agent(
        name="LeadGenCoordinator",
        model=m.coordinator,
        description="Collects and confirms a research brief, then launches the lead research pipeline.",
        instruction=prompts.COORDINATOR,
        tools=[save_research_brief, approve_research_brief],
        generate_content_config=models.generation_config(),
        sub_agents=[build_pipeline(m)],
    )
