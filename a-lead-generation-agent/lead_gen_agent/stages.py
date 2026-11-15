"""Deterministic pipeline stages implemented as custom ADK agents (`BaseAgent`).

Not every step needs an LLM. Merging, de-duplicating, fetching pages and scoring are plain code:
cheaper, faster, reproducible and unit-testable. LLM agents are used only where judgement or
open-web research is required.

State is written through `EventActions(state_delta=...)` so changes are persisted by whatever
session service is in use (in-memory, database, Vertex AI).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, ParallelAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from . import config, models, prompts
from .config import K
from .tools import web
from .tools.export import export_csv
from .tools.scoring import merge_leads, rank_leads, ranking_markdown
from .tools.search import search_tools


def _say(author: str, text: str, **state_delta: Any) -> Event:
    """An event that shows `text` to the user (or logs) and writes `state_delta` to the session."""
    return Event(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=state_delta),
    )


def summarize_leads(leads: list[dict]) -> str:
    if not leads:
        return "(none yet)"
    return "\n".join(
        f"- {l.get('company_name')} | {l.get('website') or 'no site'} | "
        f"{l.get('hq_location') or '?'} | {l.get('industry') or '?'}"
        for l in leads
    )


def _as_dict(value: Any) -> dict:
    """LLM output stored via output_key may be a dict, a JSON string or already a model."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, str):
        return parse_json_object(value)
    return {}


def parse_json_object(text: str) -> dict:
    """Leniently extract a JSON object from model text (handles ```json fences and chatter)."""
    text = text.strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


class InitResearchAgent(BaseAgent):
    """Seeds the state keys that later prompts template in, so no placeholder is ever missing."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        brief = ctx.session.state.get(K.BRIEF) or {}
        target = int(brief.get("target_lead_count", 10))
        rounds = max(1, config.max_iterations())
        # Ask each round for a share of the target, with headroom for duplicates and rejects.
        per_round = max(3, -(-int(target * 1.5) // rounds))
        yield _say(
            self.name,
            f"Starting research: target {target} leads, up to {rounds} discovery rounds.",
            **{
                K.LEADS: [],
                K.LEADS_SUMMARY: "(none yet)",
                K.GAPS: "(none - this is the first round)",
                K.LEAD_COUNT: 0,
                K.TARGET_COUNT: target,
                K.ROUND_TARGET: per_round,
                K.RAW_FINDINGS: "",
            },
        )


class LeadMergerAgent(BaseAgent):
    """Folds the structured extraction of the latest round into the de-duplicated lead pool."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        batch = _as_dict(state.get(K.BATCH))
        new = [l for l in batch.get("leads", []) if l.get("company_name")]
        pool, added = merge_leads(state.get(K.LEADS, []), new)
        yield _say(
            self.name,
            f"Discovery round merged: {len(new)} extracted, {added} new, {len(pool)} total.",
            **{K.LEADS: pool, K.LEADS_SUMMARY: summarize_leads(pool), K.LEAD_COUNT: len(pool)},
        )


class EnrichmentAgent(BaseAgent):
    """Fetches each lead's homepage concurrently and attaches contact / tech facts."""

    concurrency: int = 6

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        leads = [dict(l) for l in ctx.session.state.get(K.LEADS, [])]
        sem = asyncio.Semaphore(self.concurrency)

        async def enrich(lead: dict) -> None:
            if not lead.get("website"):
                return
            async with sem:
                lead["enrichment"] = await web.fetch_company_page(lead["website"])

        await asyncio.gather(*(enrich(l) for l in leads))
        ok = sum(1 for l in leads if (l.get("enrichment") or {}).get("ok"))
        yield _say(self.name, f"Enriched {ok}/{len(leads)} leads from their websites.", **{K.LEADS: leads})


class SignalResearchAgent(BaseAgent):
    """Runs one google_search analyst per top lead, in parallel, then folds results back in.

    The set of parallel branches is only known at run time (it depends on how many leads were
    found), so the ParallelAgent is built dynamically inside this agent.
    """

    model: Any = None  # str model name or BaseLlm

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        brief = state.get(K.BRIEF) or {}
        leads = [dict(l) for l in state.get(K.LEADS, [])]

        # Only deep-dive the most promising leads: preliminary rank using what we know so far.
        order = sorted(range(len(leads)), key=lambda i: -rank_leads([leads[i]], brief)[0]["score"])
        chosen = order[: config.deep_dive_top_n()]
        if not chosen:
            yield _say(self.name, "No leads to analyse.")
            return

        branches = []
        for i in chosen:
            lead = leads[i]
            text = prompts.SIGNAL_RESEARCHER.format(
                company=lead.get("company_name", ""),
                website=lead.get("website") or "unknown",
                product=brief.get("product_description", ""),
                signals=", ".join(brief.get("buy_signals", [])) or "any buying signal",
            )
            branches.append(
                LlmAgent(
                    name=f"SignalAnalyst_{i}",
                    model=self.model,
                    # A callable instruction bypasses {placeholder} templating, so company
                    # names containing braces cannot break the prompt.
                    instruction=lambda _ctx, t=text: t,
                    tools=search_tools(),
                    output_key=f"signals_{i}",
                    generate_content_config=models.generation_config(),
                )
            )

        async for event in ParallelAgent(name="SignalResearchFanOut", sub_agents=branches).run_async(ctx):
            yield event

        state = ctx.session.state
        for i in chosen:
            leads[i]["signals"] = str(state.get(f"signals_{i}", "")).strip()
        yield _say(self.name, f"Signal research finished for {len(chosen)} leads.", **{K.LEADS: leads})


class ScoringAgent(BaseAgent):
    """Scores, ranks, exports CSV and prepares the text blocks the report composer needs."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        brief = state.get(K.BRIEF) or {}
        ranked = rank_leads(state.get(K.LEADS, []), brief)
        target = int(brief.get("target_lead_count", len(ranked)))
        ranked = ranked[: max(target, 1)] if target else ranked
        csv_path = export_csv(ranked, config.output_dir())

        detail = []
        for i, l in enumerate(ranked, 1):
            enrich = l.get("enrichment") or {}
            detail.append(
                f"[{i}] {l.get('company_name')} (score {l['score']}) - {l.get('description', '')}\n"
                f"    Why fit: {l.get('why_fit', '') or 'n/a'}\n"
                f"    Signals: {(l.get('signals') or 'not researched').strip()}\n"
                f"    Emails on site: {', '.join(enrich.get('emails', [])) or 'none found'}\n"
                f"    Sources: {', '.join(l.get('source_urls', [])) or 'none'}"
            )
        yield _say(
            self.name,
            f"Ranked {len(ranked)} leads; CSV written to {csv_path}.",
            **{
                K.RANKED: ranked,
                K.RANKED_MD: ranking_markdown(ranked),
                K.DETAIL: "\n".join(detail),
                K.CSV_PATH: str(csv_path),
            },
        )
