"""Function tools used by the coordinator agent to capture and approve the research brief."""

from __future__ import annotations

from google.adk.tools import ToolContext

from ..config import K
from ..schemas import ResearchBrief


def render_brief(brief: dict) -> str:
    def j(v):
        return ", ".join(v) if v else "any"

    return (
        f"Product: {brief['product_description']}\n"
        f"Industries: {j(brief['target_industries'])}\n"
        f"Regions: {j(brief['regions'])}\n"
        f"Company size: {brief['company_size'] or 'any'}\n"
        f"Buyer personas: {j(brief['buyer_personas'])}\n"
        f"Buy signals: {j(brief['buy_signals'])}\n"
        f"Target lead count: {brief['target_lead_count']}\n"
        "Search angles:\n" + "\n".join(f"  - {a}" for a in brief["search_angles"])
    )


def save_research_brief(
    product_description: str,
    target_industries: list[str],
    regions: list[str],
    company_size: str,
    buyer_personas: list[str],
    buy_signals: list[str],
    target_lead_count: int,
    search_angles: list[str],
    tool_context: ToolContext,
) -> dict:
    """Save (or overwrite) the research brief so the user can review it.

    Call this once you understand the user's product, ideal customer and goals. Provide 3-5
    distinct `search_angles` (concrete web search ideas, each targeting a different slice of
    the market). Calling it again replaces the previous brief and resets approval.

    Returns:
        dict with status and a human-readable rendering to show the user.
    """
    try:
        brief = ResearchBrief(
            product_description=product_description,
            target_industries=target_industries,
            regions=regions,
            company_size=company_size,
            buyer_personas=buyer_personas,
            buy_signals=buy_signals,
            target_lead_count=max(1, min(int(target_lead_count), 50)),
            search_angles=search_angles,
        ).model_dump()
    except Exception as exc:  # validation problems go back to the model, not up the stack
        return {"status": "error", "message": str(exc)}
    tool_context.state[K.BRIEF] = brief
    tool_context.state[K.BRIEF_TEXT] = render_brief(brief)
    tool_context.state[K.APPROVED] = False
    return {"status": "saved", "brief": tool_context.state[K.BRIEF_TEXT]}


def approve_research_brief(tool_context: ToolContext) -> dict:
    """Mark the saved brief as approved by the user. Only call after the user explicitly agrees."""
    if not tool_context.state.get(K.BRIEF):
        return {"status": "error", "message": "No brief saved yet. Call save_research_brief first."}
    tool_context.state[K.APPROVED] = True
    return {"status": "approved"}
