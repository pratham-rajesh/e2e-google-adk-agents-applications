"""Run the assistant once, including the human-approval loop, and return a plain-dict result.

Shared by the demo script and the tests so there is one definition of "what a run returns".

How approval works in ADK: when a tool that needs confirmation is called, the run emits a function call
named `adk_request_confirmation` (carrying the original call) and pauses. The client answers with a
FunctionResponse whose id matches and whose payload is `{"confirmed": true|false}`; ADK then runs (or
skips) the original tool and the model continues.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Union

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.runners import InMemoryRunner
from google.genai import types

from .config import K

APP = "bug_triage"
CONFIRM_CALL = "adk_request_confirmation"

Approver = Callable[[str, dict], Union[bool, Awaitable[bool]]]


def _confirmation_requests(event: Event) -> list[dict]:
    out = []
    for fc in event.get_function_calls():
        if fc.name == CONFIRM_CALL:
            original = (fc.args or {}).get("originalFunctionCall") or {}
            out.append({"id": fc.id, "tool": original.get("name"), "args": original.get("args") or {}})
    return out


def _decision(request: dict, approved: bool) -> types.Content:
    part = types.Part(function_response=types.FunctionResponse(
        id=request["id"], name=CONFIRM_CALL, response={"confirmed": approved}))
    return types.Content(role="user", parts=[part])


async def run_once(
    agent: BaseAgent,
    request: str,
    approve: Approver | None = None,
    user_id: str = "cli-user",
    on_event: Callable[[Event], Any] | None = None,
    max_rounds: int = 6,
) -> dict:
    """Send `request`; answer each approval prompt with `approve(tool, args) -> bool`.

    With `approve=None` the run stops at the first approval prompt and reports it in `pending`.
    """
    runner = InMemoryRunner(agent=agent, app_name=APP)
    try:
        session = await runner.session_service.create_session(app_name=APP, user_id=user_id)
        message: types.Content | None = types.Content(role="user", parts=[types.Part(text=request)])
        answer, pending, decisions = "", [], []

        for _ in range(max_rounds):
            pending = []
            async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
                if on_event:
                    res = on_event(event)
                    if inspect.isawaitable(res):
                        await res
                pending.extend(_confirmation_requests(event))
                if event.author == agent.name and event.content and not event.get_function_calls():
                    text = "".join(p.text or "" for p in event.content.parts or [])
                    if text.strip():
                        answer = text.strip()
            if not pending or approve is None:
                break
            responses = []
            for req in pending:
                ok = approve(req["tool"], req["args"])
                ok = bool(await ok) if inspect.isawaitable(ok) else bool(ok)
                decisions.append({"tool": req["tool"], "args": req["args"], "approved": ok})
                responses.append(_decision(req, ok))
            # one message can carry several function responses (parallel tool calls)
            message = types.Content(role="user", parts=[p for r in responses for p in r.parts])

        state = (await runner.session_service.get_session(
            app_name=APP, user_id=user_id, session_id=session.id)).state
    finally:
        await runner.close()

    return {
        "answer": answer,
        "pending": pending if approve is None else [],
        "decisions": decisions,
        "writes": state.get(K.WRITES, 0),
        "write_log": state.get(K.WRITE_LOG, []),
        "blocked": state.get(K.BLOCKED, []),
        "best_duplicate": state.get(K.BEST_DUPLICATE),
        "research": state.get(K.RESEARCH),
    }
