"""Deterministic pipeline stages (plain code, no LLM): capture the request, write the audit record."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from . import config
from .config import K


def _say(author: str, text: str | None = None, **state_delta: Any) -> Event:
    content = types.Content(role="model", parts=[types.Part(text=text)]) if text else None
    return Event(author=author, content=content, actions=EventActions(state_delta=state_delta))


class RequestCaptureAgent(BaseAgent):
    """Stores the user's message in state so later stages (which don't see chat history) can use it,
    and resets per-run state so a reused session doesn't leak the previous run's audit log."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        parts = (ctx.user_content.parts if ctx.user_content else None) or []
        text = "\n".join(p.text for p in parts if p.text).strip()
        yield _say(self.name, **{K.REQUEST: text, K.CLI_CALLS: []})


class RunRecorderAgent(BaseAgent):
    """Writes output/run_<timestamp>.json: request, plan, every CLI call, review, final answer."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        s = ctx.session.state
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = config.output_dir() / f"run_{stamp}.json"
        record = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "request": s.get(K.REQUEST),
            "plan": s.get(K.PLAN),
            "cli_calls": s.get(K.CLI_CALLS) or [],
            "worker_result": s.get(K.RESULT),
            "review": s.get(K.REVIEW),
        }
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        yield _say(self.name, f"Run record written to {path}", **{K.RUN_FILE: str(path)})
