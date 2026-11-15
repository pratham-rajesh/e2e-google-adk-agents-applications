"""The real ADK graph against the real MCP server (a stdio subprocess per toolset).

Only the LLM replies and web search are scripted. ADK's runner, session state, `McpToolset` discovery,
the MCP stdio transport, the SQLite server, the approval pause/resume and the guardrail callbacks all run
for real, so these tests exercise the actual wiring rather than mocks of it.
"""

import json

import pytest

from bug_assistant.config import K
from bug_assistant.pipeline import Models, build_root_agent, ticket_toolsets
from bug_assistant.runner import run_once
from ticket_mcp_server.store import TicketStore

from .fake_llm import FakeLlm, call

SEARCH_CALLS: list[str] = []


async def web_search(query: str) -> dict:
    """Stand-in for the DuckDuckGo tool: canned results, no network."""
    SEARCH_CALLS.append(query)
    return {"status": "ok", "results": [{"title": "Stripe webhooks: verify signatures",
                                         "url": "https://docs.example.com/webhooks", "snippet": "..."}]}


RESEARCH = ("PROBABLE CAUSE: The webhook handler parses the body before verifying the signature.\n"
            "FIXES:\n- Verify against the raw request body.\nSOURCES:\n- Webhooks - https://docs.example.com/webhooks\n"
            "CONFIDENCE: medium (one source)")

NEW_BUG = "Webhook endpoint returns 400 'signature mismatch' for every Stripe event after we added JSON middleware"


def build(triage_script, researcher_script=None, *, confirm=True):
    triage = FakeLlm(script=triage_script)
    researcher = FakeLlm(script=researcher_script or [call("web_search", query="stripe signature mismatch"), RESEARCH])
    agent = build_root_agent(Models(triage=triage, researcher=researcher),
                             toolsets=ticket_toolsets(require_confirmation=confirm), search=[web_search])
    return agent, triage, researcher


def tool_result_texts(req) -> list[str]:
    """Every function_response the model was shown in this request, as text."""
    out = []
    for content in req.contents:
        for p in content.parts or []:
            if p.function_response:
                out.append(json.dumps(p.function_response.response, default=str))
    return out


def create_new_bug_ticket(**over):
    args = dict(title="Stripe webhook returns 400 signature mismatch after JSON middleware",
                description="Steps: send any event. Cause: body parsed before verification. Fix: use raw body. "
                            "Source: https://docs.example.com/webhooks",
                priority="high", component="payments")
    return call("create_ticket", **{**args, **over})


async def test_model_sees_mcp_tools_discovered_at_runtime():
    agent, triage, _ = build(["ok"])
    await run_once(agent, "hello", approve=lambda t, a: True)
    assert set(triage.tool_names(0)) == {"search_tickets", "get_ticket", "find_duplicates", "create_ticket",
                                         "update_ticket", "add_comment", "cause_researcher"}
    assert "DEDUPE FIRST" in triage.system_text(0)


async def test_duplicate_report_gets_a_comment_not_a_new_ticket(sandbox):
    agent, triage, researcher = build([
        call("find_duplicates", title="Checkout 500 NullPointerException discontinued product in cart",
             description="NPE in PriceCalculator.lineTotal, price is null"),
        call("get_ticket", ticket_id=1),
        call("add_comment", ticket_id=1, body="Seen again on prod 2026-11-14 with SKU 4411 (discontinued)."),
        "This duplicates ticket #1 (open, high). I added a comment with the new occurrence.",
    ])
    approvals = []
    out = await run_once(agent, "Checkout crashes with a 500 when a discontinued product is in the cart",
                         approve=lambda tool, args: approvals.append((tool, args)) or True)

    assert out["best_duplicate"]["id"] == 1 and out["best_duplicate"]["score"] >= 0.6
    assert [a[0] for a in approvals] == ["add_comment"]            # only the write needed a human
    assert out["writes"] == 1 and out["write_log"][0]["ticket_id"] == 1
    store = TicketStore(sandbox)
    assert store.count() == 8                                     # no new ticket
    assert "SKU 4411" in store.get(1)["comments"][-1]["body"]
    assert researcher.requests == []                              # a duplicate needs no research
    # the model really saw the ticket's comments through MCP
    assert any("Discontinued SKUs have price=NULL" in t for t in tool_result_texts(triage.requests[2]))
    assert "#1" in out["answer"]


async def test_new_bug_is_researched_then_filed_after_approval(sandbox):
    SEARCH_CALLS.clear()
    agent, triage, researcher = build([
        call("find_duplicates", title="Stripe webhook signature mismatch 400", description=NEW_BUG),
        call("cause_researcher", request="Stripe webhook 'signature mismatch' 400 after adding JSON body middleware"),
        create_new_bug_ticket(),
        "Filed the ticket after your approval.",
    ])
    seen = []
    out = await run_once(agent, NEW_BUG, approve=lambda tool, args: seen.append((tool, args)) or True)

    assert SEARCH_CALLS == ["stripe signature mismatch"]
    assert out["research"].startswith("PROBABLE CAUSE")           # researcher output kept via output_key
    assert "signature mismatch" in researcher.requests[0].contents[0].parts[0].text
    assert any("PROBABLE CAUSE" in t for t in tool_result_texts(triage.requests[2]))  # AgentTool returned it
    assert seen[0][0] == "create_ticket" and seen[0][1]["priority"] == "high"
    created = TicketStore(sandbox).get(9)
    assert created["component"] == "payments" and "docs.example.com" in created["description"]
    assert out["writes"] == 1 and out["write_log"][0]["tool"] == "create_ticket"
    assert out["blocked"] == []


async def test_rejected_approval_leaves_the_database_untouched(sandbox):
    agent, triage, _ = build([
        call("find_duplicates", title="Stripe webhook signature mismatch 400", description=NEW_BUG),
        create_new_bug_ticket(),
        "The ticket was not created because you rejected it.",
    ])
    out = await run_once(agent, NEW_BUG, approve=lambda tool, args: False)
    assert out["decisions"] == [{"tool": "create_ticket", "args": out["decisions"][0]["args"], "approved": False}]
    assert out["writes"] == 0
    assert TicketStore(sandbox).count() == 8
    assert any("rejected" in t for t in tool_result_texts(triage.requests[-1]))


async def test_without_an_approver_the_run_pauses_with_the_pending_call(sandbox):
    agent, *_ = build([call("find_duplicates", title="Stripe webhook mismatch", description=NEW_BUG),
                       create_new_bug_ticket(), "should never be reached"])
    out = await run_once(agent, NEW_BUG, approve=None)
    assert [p["tool"] for p in out["pending"]] == ["create_ticket"]
    assert out["pending"][0]["args"]["component"] == "payments"
    assert out["writes"] == 0 and TicketStore(sandbox).count() == 8


async def test_confirmation_can_be_disabled_for_unattended_use(sandbox):
    agent, *_ = build([call("find_duplicates", title="Stripe webhook mismatch", description=NEW_BUG),
                       create_new_bug_ticket(), "done"], confirm=False)
    out = await run_once(agent, NEW_BUG, approve=None)
    assert out["pending"] == [] and out["writes"] == 1 and TicketStore(sandbox).count() == 9


# ---------------------------------------------------------------- policy guardrails (before any human is asked)

async def test_create_without_dedupe_check_is_refused_before_approval(sandbox):
    agent, triage, _ = build([create_new_bug_ticket(), "I need to search first."])
    asked = []
    out = await run_once(agent, NEW_BUG, approve=lambda t, a: asked.append(t) or True)
    assert asked == []                                            # the human was never bothered
    assert out["blocked"][0]["tool"] == "create_ticket" and "find_duplicates" in out["blocked"][0]["reason"]
    assert out["writes"] == 0 and TicketStore(sandbox).count() == 8
    assert any("refused_by_policy" in t for t in tool_result_texts(triage.requests[-1]))


async def test_near_duplicate_create_is_refused_and_points_at_the_ticket(sandbox):
    agent, triage, _ = build([
        call("find_duplicates", title="Checkout 500 NullPointerException discontinued product",
             description="price is null in PriceCalculator"),
        call("create_ticket", title="Checkout 500 for discontinued product", description="NPE in PriceCalculator"),
        "Ticket #1 already covers this.",
    ])
    out = await run_once(agent, "checkout 500", approve=lambda t, a: True)
    assert out["writes"] == 0 and "#1" in out["blocked"][0]["reason"] and "add_comment" in out["blocked"][0]["reason"]
    assert TicketStore(sandbox).count() == 8


async def test_secrets_never_reach_the_ticket_store(sandbox):
    agent, *_ = build([
        call("find_duplicates", title="Stripe webhook signature mismatch", description="x"),
        create_new_bug_ticket(description="Config: api_key = sk_live_51Habcdefghijklmnop and it fails"),
        create_new_bug_ticket(description="Config: api_key = <redacted> and it fails"),
        "Filed with the key masked.",
    ])
    out = await run_once(agent, NEW_BUG, approve=lambda t, a: True)
    assert len(out["blocked"]) == 1 and "looks like it contains" in out["blocked"][0]["reason"]
    assert out["writes"] == 1
    assert "sk_live" not in TicketStore(sandbox).get(9)["description"]


async def test_agent_cannot_close_tickets(sandbox):
    agent, *_ = build([call("update_ticket", ticket_id=4, status="closed"),
                       call("update_ticket", ticket_id=4, status="resolved"), "ok"])
    out = await run_once(agent, "close the slow search ticket", approve=lambda t, a: True)
    assert "only a human may close" in out["blocked"][0]["reason"]
    assert TicketStore(sandbox).get(4)["status"] == "resolved" and out["writes"] == 1


async def test_write_budget_is_enforced(sandbox, monkeypatch):
    monkeypatch.setenv("BUGASSIST_MAX_WRITES", "2")
    agent, *_ = build([call("add_comment", ticket_id=1, body="one"), call("add_comment", ticket_id=1, body="two"),
                       call("add_comment", ticket_id=1, body="three"), "stopped"])
    out = await run_once(agent, "spam comments", approve=lambda t, a: True)
    assert out["writes"] == 2 and "budget" in out["blocked"][0]["reason"]
    assert len(TicketStore(sandbox).get(1)["comments"]) == 3     # 1 seeded + 2 allowed


async def test_server_validation_error_reaches_the_model_as_a_result(sandbox):
    agent, triage, _ = build([call("find_duplicates", title="Stripe webhook mismatch", description=NEW_BUG),
                              create_new_bug_ticket(priority="urgent"), "retrying with a valid priority"])
    out = await run_once(agent, NEW_BUG, approve=lambda t, a: True)
    assert out["writes"] == 0                                     # status was "error", so not counted
    assert any("priority must be one of" in t for t in tool_result_texts(triage.requests[-1]))
    assert TicketStore(sandbox).count() == 8
