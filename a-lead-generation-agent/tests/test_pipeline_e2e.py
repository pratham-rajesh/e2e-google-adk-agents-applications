"""Runs the real agent graph (coordinator -> transfer -> loop -> stages -> report) offline.

Only the LLM calls and the web fetch are scripted; ADK's runner, session state, templating,
tool dispatch, agent transfer, LoopAgent escalation and ParallelAgent are all the real thing.
"""

import csv
import json
from pathlib import Path

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types

from lead_gen_agent.config import K
from lead_gen_agent.pipeline import Models, build_root_agent
from lead_gen_agent.tools import web

from .fake_llm import FakeLlm, call

APP, USER = "lead_gen_test", "tester"

ROUND1 = {
    "leads": [
        {"company_name": "PayNova", "website": "https://paynova.in", "hq_location": "Bengaluru, India",
         "industry": "fintech payments", "description": "UPI payment gateway",
         "why_fit": "Hiring compliance engineers", "source_urls": ["https://news.example/paynova"]},
        {"company_name": "LedgerLeaf", "website": "https://ledgerleaf.sg", "hq_location": "Singapore",
         "industry": "fintech", "description": "SME accounting", "source_urls": ["https://news.example/ll"]},
    ]
}
ROUND2 = {
    "leads": [
        {"company_name": "PayNova Ltd", "website": "https://www.paynova.in/"},  # duplicate
        {"company_name": "Mining Co {braces}", "website": "https://mining.cl", "hq_location": "Chile",
         "industry": "mining", "source_urls": ["https://news.example/mine"]},
    ]
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADGEN_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("LEADGEN_MAX_ITERATIONS", "3")
    monkeypatch.setenv("LEADGEN_DEEP_DIVE_TOP_N", "3")

    async def fake_fetch(url, **_):
        if "paynova" in url:
            return {"url": url, "ok": True, "emails": ["sales@paynova.in"], "social": [], "tech": []}
        return {"url": url, "ok": False, "error": "HTTPStatusError: 404"}

    monkeypatch.setattr(web, "fetch_company_page", fake_fetch)


def _models():
    coordinator = FakeLlm(script=[
        call("save_research_brief", product_description="Fraud detection API",
             target_industries=["fintech", "payments"], regions=["India", "Singapore"],
             company_size="50-500", buyer_personas=["Head of Risk"],
             buy_signals=["hiring", "compliance"], target_lead_count=3,
             search_angles=["fintech India fraud losses", "MAS compliance payments Singapore"]),
        "Here is the brief. Approve?",
        call("approve_research_brief"),
        call("transfer_to_agent", agent_name="LeadResearchPipeline"),
    ])
    researcher = FakeLlm(script=["Company: PayNova ...", "Company: Mining Co ..."])
    extractor = FakeLlm(script=[json.dumps(ROUND1), json.dumps(ROUND2)])
    evaluator = FakeLlm(script=["- need more Singapore leads", call("exit_loop")])
    signals = FakeLlm(script=["- Hiring a Head of Risk (https://jobs.example/1)"])
    composer = FakeLlm(script=["# Lead report\nTop lead: PayNova"])
    m = Models(coordinator=coordinator, researcher=researcher, extractor=extractor,
               evaluator=evaluator, signals=signals, composer=composer)
    return m


async def _say(runner, session_id, message):
    events = []
    async for ev in runner.run_async(
        user_id=USER, session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        events.append(ev)
    return events


async def test_full_flow():
    m = _models()
    runner = InMemoryRunner(agent=build_root_agent(m), app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=USER)

    # Turn 1: user describes the product; coordinator saves the brief and asks for approval.
    ev1 = await _say(runner, session.id, "Find me customers for my fraud detection API")
    s = (await runner.session_service.get_session(app_name=APP, user_id=USER, session_id=session.id)).state
    assert s[K.BRIEF]["target_lead_count"] == 3 and s[K.APPROVED] is False
    assert "Approve?" in ev1[-1].content.parts[0].text
    assert K.RANKED not in s  # nothing expensive ran yet

    # Turn 2: user approves -> coordinator approves + transfers -> pipeline runs to the end.
    ev2 = await _say(runner, session.id, "looks good, go")
    s = (await runner.session_service.get_session(app_name=APP, user_id=USER, session_id=session.id)).state

    authors = [e.author for e in ev2]
    for expected in ("InitResearch", "DiscoveryResearcher", "LeadExtractor", "LeadMerger",
                     "CoverageEvaluator", "Enrichment", "SignalAnalyst_0", "Scoring", "ReportComposer"):
        assert expected in authors, f"{expected} never ran; saw {sorted(set(authors))}"

    # The loop ran exactly twice: round 1 evaluator gave gaps, round 2 evaluator exit_loop'd.
    assert len(m.researcher.requests) == 2 and len(m.evaluator.requests) == 2
    assert "need more Singapore" in s[K.GAPS]

    # State templating reached the prompts (brief + gaps from round 1 flow into round 2).
    assert "Fraud detection API" in m.researcher.system_text(0)
    assert "need more Singapore leads" in m.researcher.system_text(1)
    assert "PayNova" in m.researcher.system_text(1)  # "do not repeat" list

    # Dedup: PayNova Ltd collapsed into PayNova; 3 unique leads total.
    names = [l["company_name"] for l in s[K.LEADS]]
    assert len(names) == 3 and "PayNova Ltd" not in names

    # Enrichment and deterministic scoring.
    ranked = s[K.RANKED]
    assert ranked[0]["company_name"] == "PayNova"
    assert ranked[0]["enrichment"]["emails"] == ["sales@paynova.in"]
    assert ranked[0]["score"] > ranked[-1]["score"]
    assert ranked[-1]["company_name"].startswith("Mining")

    # Top-N (3) leads got signal research, including the one whose name contains braces.
    assert sum(1 for l in s[K.LEADS] if l.get("signals")) == 3

    # Export + final report.
    rows = list(csv.DictReader(Path(s[K.CSV_PATH]).open(encoding="utf-8")))
    assert [r["company_name"] for r in rows][0] == "PayNova"
    assert "Lead report" in ev2[-1].content.parts[0].text
    assert "| 1 | PayNova" in m.composer.system_text()


async def test_pipeline_refuses_without_approved_brief():
    from lead_gen_agent.pipeline import build_pipeline

    m = _models()
    runner = InMemoryRunner(agent=build_pipeline(m), app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=USER)
    events = await _say(runner, session.id, "run it")
    assert "hasn't been approved" in events[-1].content.parts[0].text
    assert not m.researcher.requests  # no model call was made


async def test_approve_without_brief_is_rejected():
    from lead_gen_agent.tools.brief import approve_research_brief

    class Ctx:
        state = {}

    assert approve_research_brief(Ctx())["status"] == "error"


def test_package_exposes_root_agent_for_adk_cli():
    import lead_gen_agent

    root = lead_gen_agent.agent.root_agent
    assert root.name == "LeadGenCoordinator"
    assert [a.name for a in root.sub_agents] == ["LeadResearchPipeline"]
    stage_names = [a.name for a in root.sub_agents[0].sub_agents]
    assert stage_names == ["InitResearch", "DiscoveryLoop", "Enrichment", "SignalResearch",
                           "Scoring", "ReportComposer"]
