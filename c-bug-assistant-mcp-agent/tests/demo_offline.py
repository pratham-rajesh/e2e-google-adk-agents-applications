"""Scripted LLMs + a stand-in web search, used by `scripts/run_pipeline.py --offline`.

Only the model replies and the web search are stand-ins. ADK's runner, the McpToolset, the stdio MCP server
and its SQLite database, the approval pause/resume and the guardrail callbacks all run for real.
"""

from bug_assistant.pipeline import Models, build_root_agent, ticket_toolsets

from .fake_llm import FakeLlm, call

DUPLICATE_REPORT = ("Customers get an HTTP 500 at checkout whenever a discontinued product is in their cart. "
                    "Logs show a NullPointerException in PriceCalculator.")

NEW_REPORT = ("Our Stripe webhook endpoint answers 400 'signature mismatch' for every event since we added the "
              "express.json() middleware. Nothing else changed.")

RESEARCH = """PROBABLE CAUSE: Stripe signs the raw request bytes. express.json() consumes and re-serialises the body before the handler verifies the signature, so the digest no longer matches.
FIXES:
- Mount the webhook route with express.raw({type: 'application/json'}) BEFORE the global JSON middleware.
- Pass the raw Buffer (not req.body as parsed JSON) to stripe.webhooks.constructEvent.
SOURCES:
- Stripe docs: Check the webhook signatures - https://docs.stripe.com/webhooks#verify-manually
CONFIDENCE: high (matches the documented behaviour exactly)"""


async def web_search(query: str) -> dict:
    """Canned search result standing in for DuckDuckGo / Google Search."""
    return {"status": "ok", "results": [{
        "title": "Check the webhook signatures", "url": "https://docs.stripe.com/webhooks#verify-manually",
        "snippet": "Stripe requires the raw body of the request to verify the signature..."}]}


def duplicate_agent():
    triage = FakeLlm(script=[
        call("find_duplicates", title="Checkout HTTP 500 NullPointerException with discontinued product in cart",
             description="NullPointerException in PriceCalculator when a discontinued product is in the cart"),
        call("get_ticket", ticket_id=1),
        call("add_comment", ticket_id=1,
             body="New report from support (2026-11-15): still happening in production for carts containing "
                  "discontinued products. Same NullPointerException in PriceCalculator."),
        "This is a duplicate of ticket #1 ('Checkout fails with HTTP 500 ... discontinued product', open, high). "
        "Its comments already identify the cause (discontinued SKUs have price=NULL after the catalogue import), "
        "so I did not open a new ticket. With your approval I added a comment recording this new occurrence.",
    ])
    researcher = FakeLlm(script=["(not needed for a duplicate)"])
    return build_root_agent(Models(triage=triage, researcher=researcher), toolsets=ticket_toolsets(), search=[web_search])


def new_bug_agent():
    triage = FakeLlm(script=[
        call("find_duplicates", title="Stripe webhook returns 400 signature mismatch",
             description="Every Stripe event fails signature verification after adding express.json() middleware"),
        call("cause_researcher", request="Stripe webhook endpoint returns 400 'signature mismatch' for every "
                                         "event after adding express.json() middleware (Node/Express)."),
        # An over-eager first attempt that pastes a (fake) key from the logs: the guardrail must stop it.
        call("create_ticket", title="Stripe webhook returns 400 'signature mismatch' after adding express.json()",
             description="Config in the logs: webhook_secret = whsec_live_8f2a91c4d7e3b6a0. Cause: raw body is "
                         "re-serialised by the JSON middleware.", priority="high", component="payments"),
        call("create_ticket", title="Stripe webhook returns 400 'signature mismatch' after adding express.json()",
             description=("Steps: send any Stripe test event to /webhooks/stripe. Observed: 400 signature mismatch. "
                          "Expected: 200.\nProbable cause: Stripe signs the raw bytes; express.json() re-serialises "
                          "the body before verification.\nProposed fix: mount express.raw({type:'application/json'}) "
                          "on this route before the global JSON middleware and pass the Buffer to "
                          "stripe.webhooks.constructEvent.\nSource: https://docs.stripe.com/webhooks#verify-manually"),
             priority="high", component="payments"),
        "No existing ticket matched (closest was below the duplicate threshold). Researched cause: the JSON "
        "middleware changes the bytes Stripe signed. Fix: use express.raw() on the webhook route. I filed it "
        "as a high-priority 'payments' ticket after you approved it; my first draft contained a webhook secret "
        "from the logs, which the secret guardrail refused, so the ticket text is redacted.",
    ])
    researcher = FakeLlm(script=[call("web_search", query="stripe webhook signature mismatch express.json raw body"),
                                 RESEARCH])
    return build_root_agent(Models(triage=triage, researcher=researcher), toolsets=ticket_toolsets(), search=[web_search])
