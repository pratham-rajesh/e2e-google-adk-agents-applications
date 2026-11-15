"""Scripted models + canned web results used by `scripts/run_pipeline.py --offline`.

Everything marked ILLUSTRATIVE below is invented demo data, not real research output.
"""

import json

from lead_gen_agent import config
from lead_gen_agent.pipeline import Models, build_root_agent
from lead_gen_agent.tools import web

from .fake_llm import FakeLlm, call

_ROUND1 = {"leads": [  # ILLUSTRATIVE
    {"company_name": "PayNova", "website": "https://paynova.example", "hq_location": "Bengaluru, India",
     "industry": "fintech payments", "size_hint": "200 employees", "description": "UPI payment gateway for merchants",
     "why_fit": "Hiring compliance and risk engineers", "source_urls": ["https://news.example/paynova"]},
    {"company_name": "LedgerLeaf", "website": "https://ledgerleaf.example", "hq_location": "Singapore",
     "industry": "fintech", "description": "SME accounting and payouts",
     "why_fit": "Series B announced; compliance expansion", "source_urls": ["https://news.example/ledgerleaf"]},
]}
_ROUND2 = {"leads": [  # ILLUSTRATIVE (one duplicate on purpose)
    {"company_name": "PayNova Ltd", "website": "https://www.paynova.example/"},
    {"company_name": "RemitRoute", "website": "https://remitroute.example", "hq_location": "Mumbai, India",
     "industry": "payments", "description": "Cross-border remittance", "source_urls": ["https://news.example/remitroute"]},
]}


async def _fake_fetch(url, **_):
    if "paynova" in url:
        return {"url": url, "ok": True, "emails": ["partnerships@paynova.example"],
                "social": ["https://www.linkedin.com/company/paynova"], "tech": ["Stripe"]}
    return {"url": url, "ok": False, "error": "offline demo: no network"}


def build_offline_agent():
    web.fetch_company_page = _fake_fetch  # demo only
    config.K  # noqa: B018  (keeps import explicit)
    m = Models(
        coordinator=FakeLlm(script=[
            call("save_research_brief", product_description="Fraud-detection API for payment providers",
                 target_industries=["fintech", "payments"], regions=["India", "Singapore"],
                 company_size="50-500 employees", buyer_personas=["Head of Risk", "CTO"],
                 buy_signals=["hiring", "series b", "compliance"], target_lead_count=5,
                 search_angles=["Indian payment aggregators hiring risk engineers",
                                "Singapore fintech Series B 2025 compliance",
                                "RBI/MAS fraud-control mandates affecting payment firms"]),
            "Here is the proposed research brief (above). Reply 'go' to approve or tell me what to change.",
            call("approve_research_brief"),
            call("transfer_to_agent", agent_name="LeadResearchPipeline"),
        ]),
        researcher=FakeLlm(script=["(round 1 research notes)", "(round 2 research notes)"]),
        extractor=FakeLlm(script=[json.dumps(_ROUND1), json.dumps(_ROUND2)]),
        evaluator=FakeLlm(script=["- Need more India coverage; try remittance and lending firms.", call("exit_loop")]),
        signals=FakeLlm(script=["- Hiring a Head of Fraud Risk (https://jobs.example/1)\n- Raised Series B (https://news.example/b)"]),
        composer=FakeLlm(script=["# Lead report (OFFLINE DEMO - illustrative data)\n\nSee ranking table in the trace state."]),
    )
    return build_root_agent(m)
