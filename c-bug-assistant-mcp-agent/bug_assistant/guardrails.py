"""Policy guardrails around the MCP *write* tools, as ADK tool callbacks.

Two layers protect the ticket tracker:

1. `McpToolset(require_confirmation=True)` on the write toolset: ADK pauses the run and asks a human to
   approve every create/update/comment (human-in-the-loop). That is the framework's mechanism.
2. The callbacks below enforce *policy* before a human is even asked, so they never see a request that
   breaks the rules: dedupe first, no near-duplicates, no secrets in ticket text, no closing tickets,
   and a per-session write budget. They also keep an audit trail in session state.

A `before_tool_callback` that returns a dict skips the real tool and hands that dict to the model as the
tool result, so the model reads the reason and corrects itself.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import config
from .config import K

# Imported from the server package so the list of write tools is defined in exactly one place.
from ticket_mcp_server.server import WRITE_TOOLS

DEDUPE_TOOLS = ("find_duplicates", "search_tickets")

_SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("API secret key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    ("payment/webhook secret", re.compile(r"\b(?:sk|rk|whsec)_(?:live|test)?_?[A-Za-z0-9]{12,}")),
    # NAME = value, where NAME contains password/secret/token/api key (so DB_PASSWORD, webhook_secret match too)
    ("credential assignment", re.compile(
        r"(?i)[\w-]*(?:password|passwd|secret|api[_-]?key|token)[\w-]*\s*[:=]\s*['\"]?[^\s'\"<*\[{][^\s'\"]{7,}")),  # masked values (<redacted>, ****) pass
]


def find_secret(text: str) -> str | None:
    """Name of the first secret-looking pattern in `text`, or None."""
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text or ""):
            return name
    return None


def unwrap(tool_response: Any) -> dict:
    """The server's JSON result, whatever shape ADK delivered it in.

    McpTool returns the MCP result as `{"content": [{"type": "text", "text": "<json>"}], ...}`
    (or `structuredContent` when the server declares an output schema). Errors raised by ADK itself
    (e.g. a pending confirmation) come back as `{"error": "..."}`.
    """
    if not isinstance(tool_response, dict):
        return {}
    if isinstance(tool_response.get("structuredContent"), dict):
        return tool_response["structuredContent"]
    for part in tool_response.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            try:
                data = json.loads(part.get("text", ""))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return tool_response


def _refuse(tool_context, tool_name: str, args: dict, reason: str) -> dict:
    blocked = list(tool_context.state.get(K.BLOCKED, []))
    blocked.append({"tool": tool_name, "reason": reason})
    tool_context.state[K.BLOCKED] = blocked
    return {"status": "refused_by_policy", "message": reason}


def guard_tool_call(tool, args: dict[str, Any], tool_context) -> dict | None:
    """`before_tool_callback`: return a dict to refuse the call, None to let it through."""
    name = tool.name
    if name not in WRITE_TOOLS:
        return None
    state = tool_context.state

    if state.get(K.WRITES, 0) >= config.max_writes():
        return _refuse(tool_context, name, args, (
            f"Write budget reached ({config.max_writes()} ticket changes this session). "
            "Summarise what you did and ask the user to start a new session for more changes."))

    text = " ".join(str(args.get(f, "")) for f in ("title", "description", "body"))
    secret = find_secret(text)
    if secret:
        return _refuse(tool_context, name, args, (
            f"The text looks like it contains a {secret}. Remove or mask it (e.g. '<redacted>') "
            "and call the tool again."))

    if name == "create_ticket":
        if not state.get(K.DEDUPE_CHECKED):
            return _refuse(tool_context, name, args, (
                "Policy: search for existing tickets first. Call find_duplicates with this bug's "
                "title and description, then decide between a new ticket and a comment on an existing one."))
        best = state.get(K.BEST_DUPLICATE) or {}
        if best.get("score", 0.0) >= config.duplicate_block_score():
            return _refuse(tool_context, name, args, (
                f"Policy: ticket #{best.get('id')} ('{best.get('title')}') matches this report "
                f"(score {best.get('score')}). Do not create a duplicate: read it with get_ticket and, "
                "if this is a new occurrence or has new information, call add_comment on it instead."))

    if name == "update_ticket" and str(args.get("status", "")).strip().lower() == "closed":
        return _refuse(tool_context, name, args, (
            "Policy: only a human may close a ticket. Set status to 'resolved' if the fix is confirmed, "
            "or add a comment."))

    return None


def record_tool_result(tool, args: dict[str, Any], tool_context, tool_response: Any) -> dict | None:
    """`after_tool_callback`: update dedupe/audit state from what the server actually returned."""
    name = tool.name
    data = unwrap(tool_response)
    ok = data.get("status") == "ok"
    state = tool_context.state

    if name in DEDUPE_TOOLS and ok:
        state[K.DEDUPE_CHECKED] = True
        if name == "find_duplicates":
            rows = data.get("duplicates") or []
            top = rows[0] if rows else {}
            state[K.BEST_DUPLICATE] = (
                {"id": top.get("id"), "score": top.get("score", 0.0), "title": top.get("title")} if top else {}
            )

    if name in WRITE_TOOLS and ok:
        state[K.WRITES] = state.get(K.WRITES, 0) + 1
        ticket = data.get("ticket") or {}
        log = list(state.get(K.WRITE_LOG, []))
        log.append({"tool": name, "ticket_id": ticket.get("id"), "args": {k: v for k, v in args.items()}})
        state[K.WRITE_LOG] = log
    return None  # keep the real response
