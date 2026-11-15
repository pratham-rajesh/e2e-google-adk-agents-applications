"""Audit trail: every Gemini CLI invocation is appended to session state so the reviewer (an LLM)
and the run record (code) can see exactly what was asked and what came back."""

from __future__ import annotations

from typing import Any, MutableMapping

from ..config import K


def record_call(state: MutableMapping[str, Any], task: str, result: dict) -> None:
    calls = list(state.get(K.CLI_CALLS) or [])  # copy + reassign so ADK tracks the state delta
    calls.append(
        {
            "n": len(calls) + 1,
            "task": task[:2000],
            "subdirectory": result.get("subdirectory"),
            "status": result.get("status"),
            "exit_code": result.get("exit_code"),
            "duration_s": result.get("duration_s"),
            "truncated": bool(result.get("truncated")),
            "response_chars": len(result.get("response") or ""),
            "message": result.get("message"),
        }
    )
    state[K.CLI_CALLS] = calls


def calls_summary(calls: list[dict]) -> str:
    if not calls:
        return "(Gemini CLI was never called)"
    lines = []
    for c in calls:
        extra = f" - {c['message']}" if c.get("message") else ""
        lines.append(
            f"{c['n']}. status={c['status']} dir={c['subdirectory']} {c.get('duration_s')}s "
            f"response_chars={c['response_chars']}{' (truncated)' if c['truncated'] else ''}{extra}"
        )
    return "\n".join(lines)
