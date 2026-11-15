"""Run the pipeline once and return a plain-dict result. Shared by the HTTP server, the demo script
and the tests, so there is a single definition of "what a run returns"."""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.runners import InMemoryRunner
from google.genai import types

from .config import K

APP = "gemini_cli_pipeline"


def parse_verdict(review: str | None) -> str:
    m = re.search(r"VERDICT:\s*(PASS|PARTIAL|FAIL)", review or "", re.IGNORECASE)
    return m.group(1).upper() if m else "UNKNOWN"


def final_answer(review: str | None) -> str:
    """The reviewer's `FINAL ANSWER:` section (falls back to the whole review)."""
    text = review or ""
    m = re.search(r"FINAL ANSWER:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    return (m.group(1) if m else text).strip()


async def run_once(
    agent: BaseAgent,
    request: str,
    user_id: str = "api-user",
    on_event: Callable[[Event], Any] | None = None,
) -> dict:
    runner = InMemoryRunner(agent=agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=user_id)
    message = types.Content(role="user", parts=[types.Part(text=request)])
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
        if on_event:
            res = on_event(event)
            if inspect.isawaitable(res):
                await res
    state = (await runner.session_service.get_session(app_name=APP, user_id=user_id, session_id=session.id)).state
    review = state.get(K.REVIEW)
    return {
        "verdict": parse_verdict(review),
        "answer": final_answer(review),
        "plan": state.get(K.PLAN),
        "cli_calls": state.get(K.CLI_CALLS) or [],
        "run_record": state.get(K.RUN_FILE),
    }
