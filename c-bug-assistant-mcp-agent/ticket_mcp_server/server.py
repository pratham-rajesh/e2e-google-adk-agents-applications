"""The ticket tracker exposed as an MCP server.

MCP (Model Context Protocol) is a standard way to publish tools to any agent framework. This server
publishes six tools; the ADK agent discovers them at runtime through `McpToolset` and never imports
this package. Tools return plain dicts with `status` ("ok" | "error"): a refused request becomes an
error result the model can read and correct, rather than a protocol-level failure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

try:  # mcp >= 2 renamed FastMCP to MCPServer; support both major versions
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - exercised only on mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

from .seed import SEED_TICKETS
from .store import PRIORITIES, STATUSES, TicketError, TicketStore

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)

# Tool names that change data. The agent's guardrail imports this list, so it is defined once, here.
WRITE_TOOLS = ("create_ticket", "update_ticket", "add_comment")
READ_TOOLS = ("search_tickets", "get_ticket", "find_duplicates")

INSTRUCTIONS = (
    "Bug ticket tracker. Always call find_duplicates (or search_tickets) before create_ticket. "
    f"Statuses: {', '.join(STATUSES)}. Priorities: {', '.join(PRIORITIES)}."
)


def _guard(fn: Callable[..., Any]) -> Callable[..., dict]:
    """Turn store validation errors into `{"status": "error"}` results; wrap successes with `status: ok`."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = fn(*args, **kwargs)
        except TicketError as exc:
            return {"status": "error", "message": str(exc)}
        return {"status": "ok", **result}

    return wrapper


def build_server(store: TicketStore) -> Any:
    mcp = _Server("ticket-tracker", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    @_guard
    def search_tickets(query: str = "", status: str = "", component: str = "", priority: str = "", limit: int = 10) -> dict[str, Any]:
        """Search tickets by keywords, optionally filtered. An empty query lists the newest tickets.

        Args:
            query: Keywords such as an error message, exception name or endpoint.
            status: Optional filter: open, in_progress, resolved or closed.
            component: Optional filter, e.g. checkout, auth, backend.
            priority: Optional filter: low, medium, high or critical.
            limit: Maximum tickets to return (1-50).
        """
        rows = store.search(query, status or None, component or None, priority or None, limit)
        return {"count": len(rows), "tickets": rows}

    @mcp.tool(annotations=READ_ONLY)
    @_guard
    def get_ticket(ticket_id: int) -> dict[str, Any]:
        """Return one ticket with its full description and every comment (resolutions are often in comments)."""
        return {"ticket": store.get(ticket_id)}

    @mcp.tool(annotations=READ_ONLY)
    @_guard
    def find_duplicates(title: str, description: str = "", threshold: float = 0.35) -> dict[str, Any]:
        """Find existing tickets that describe the same problem as a new bug report.

        Call this BEFORE create_ticket. A score above ~0.6 is very likely a duplicate; 0.35-0.6 is
        "related, read it". `matched_terms` shows why each ticket matched.

        Args:
            title: Title of the bug being reported.
            description: Longer description, error text or stack trace excerpt.
            threshold: Minimum similarity score (0-1).
        """
        rows = store.find_duplicates(title, description, threshold)
        return {"count": len(rows), "duplicates": rows}

    @mcp.tool(annotations=WRITE)
    @_guard
    def create_ticket(title: str, description: str, priority: str = "medium", component: str = "general") -> dict[str, Any]:
        """Create a new bug ticket. Only after find_duplicates found nothing that matches.

        Args:
            title: One-line summary (max 200 chars) naming the symptom.
            description: Steps to reproduce, observed vs expected behaviour, probable cause, proposed fix.
            priority: low, medium, high or critical.
            component: Area of the product, e.g. checkout, auth, backend.
        """
        return {"ticket": store.create(title, description, priority, component, reporter="bug-assistant")}

    @mcp.tool(annotations=WRITE)
    @_guard
    def update_ticket(ticket_id: int, status: str = "", priority: str = "", component: str = "") -> dict[str, Any]:
        """Change a ticket's status, priority or component. Empty arguments are left unchanged.

        Args:
            ticket_id: Id of the ticket.
            status: open, in_progress, resolved or closed.
            priority: low, medium, high or critical.
            component: New component name.
        """
        return {"ticket": store.update(ticket_id, status or None, priority or None, component or None)}

    @mcp.tool(annotations=WRITE)
    @_guard
    def add_comment(ticket_id: int, body: str) -> dict[str, Any]:
        """Add a comment to an existing ticket, e.g. a new occurrence, an analysis or a proposed fix."""
        return {"ticket": store.add_comment(ticket_id, body, author="bug-assistant")}

    return mcp


def default_db_path() -> Path:
    return Path(os.getenv("BUGASSIST_DB", Path(__file__).resolve().parents[1] / "data" / "tickets.db"))


def create_server(db_path: str | Path | None = None, seed: bool = True) -> Any:
    store = TicketStore(db_path or default_db_path())
    if seed:
        store.seed(SEED_TICKETS)
    return build_server(store)
