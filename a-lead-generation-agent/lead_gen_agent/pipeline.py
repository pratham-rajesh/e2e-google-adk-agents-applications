"""Builds the agent graph. `models` lets tests swap in scripted fake models per role."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.adk.agents import Agent, LlmAgent, LoopAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import exit_loop, google_search
from google.genai import types

from . import config, prompts
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


@dataclass
class Models:
    """One model (name or BaseLlm instance) per role."""

    coordinator: Any = config.FAST_MODEL
    researcher: Any = config.FAST_MODEL
    extractor: Any = config.FAST_MODEL
    evaluator: Any = config.FAST_MODEL
    signals: Any = config.FAST_MODEL
    composer: Any = config.SMART_MODEL


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
            # google_search is a built-in tool and cannot be combined with output_schema, hence the
            # researcher -> extractor split: free-text research first, structured extraction second.
            LlmAgent(
                name="DiscoveryResearcher",
                model=m.researcher,
                instruction=prompts.DISCOVERY_RESEARCHER,
                tools=[google_search],
                output_key=K.RAW_FINDINGS,
            ),
            LlmAgent(
                name="LeadExtractor",
                model=m.extractor,
                instruction=prompts.LEAD_EXTRACTOR,
                output_schema=LeadBatch,
                output_key=K.BATCH,
                include_contents="none",
            ),
            LeadMergerAgent(name="LeadMerger"),
            LlmAgent(
                name="CoverageEvaluator",
                model=m.evaluator,
                instruction=prompts.COVERAGE_EVALUATOR,
                tools=[exit_loop],
                output_key=K.GAPS,
                include_contents="none",
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
        sub_agents=[build_pipeline(m)],
    )
