"""Environment-driven settings and the session-state keys the agent uses (all env vars: BUGASSIST_*)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class K:
    """Session-state keys. Written by the guardrail callbacks, read by tests and the run script."""

    DEDUPE_CHECKED = "dedupe_checked"      # bool: find_duplicates/search_tickets ran this session
    BEST_DUPLICATE = "best_duplicate"      # {"id", "score", "title"} from the latest find_duplicates
    WRITES = "writes_done"                 # int: successful ticket writes
    WRITE_LOG = "write_log"                # list[dict]: audit trail of every successful write
    BLOCKED = "blocked_calls"              # list[dict]: write attempts the guardrail refused
    RESEARCH = "research_notes"            # output_key of the cause_researcher agent


def db_path() -> str:
    return os.getenv("BUGASSIST_DB", str(ROOT / "data" / "tickets.db"))


def max_writes() -> int:
    """Ticket writes allowed per session (a runaway-loop brake, not a business rule)."""
    return int(os.getenv("BUGASSIST_MAX_WRITES", "3"))


def duplicate_block_score() -> float:
    """create_ticket is refused when the latest duplicate check found a ticket at/above this score."""
    return float(os.getenv("BUGASSIST_DUP_BLOCK", "0.6"))


def require_confirmation() -> bool:
    """Human approval before every write tool. Only switch off for unattended batch use."""
    return os.getenv("BUGASSIST_REQUIRE_CONFIRMATION", "1").lower() not in ("0", "false", "no")


def server_command() -> tuple[str, list[str]]:
    """How the agent launches the MCP server: the same interpreter, `-m ticket_mcp_server`."""
    return sys.executable, ["-m", "ticket_mcp_server"]


def server_env() -> dict[str, str]:
    """Environment for the server subprocess. It needs only the DB path, never API keys."""
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "PYTHONPATH", "VIRTUAL_ENV")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["BUGASSIST_DB"] = db_path()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
    return env
