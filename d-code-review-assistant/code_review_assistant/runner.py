"""Run the assistant programmatically and return plain dicts (shared by the scripts, the server and the tests).

`ReviewSession` keeps one ADK session alive across turns, which is what makes "review, then fix it" work: the second
turn finds the reviewed code, tests and report in session state and the artifacts in the artifact service.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from google.adk.agents import BaseAgent
from google.adk.artifacts import FileArtifactService, InMemoryArtifactService
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from . import config
from .config import APP_NAME, K


def make_artifact_service():
    """In memory by default; a directory when CODEREVIEW_ARTIFACT_DIR is set. For GCS see the README."""
    d = config.artifact_dir()
    return FileArtifactService(root_dir=d) if d else InMemoryArtifactService()


class ReviewSession:
    def __init__(self, agent: BaseAgent, user_id: str = "cli-user", artifact_service: Any = None) -> None:
        self.agent, self.user_id = agent, user_id
        self.artifacts = artifact_service or make_artifact_service()
        self.sessions = InMemorySessionService()
        self.runner = Runner(agent=agent, app_name=APP_NAME, session_service=self.sessions,
                             artifact_service=self.artifacts, auto_create_session=False)
        self.session_id: str | None = None

    async def ask(self, text: str, on_event: Callable[[Event], Any] | None = None) -> dict:
        if self.session_id is None:
            self.session_id = (await self.sessions.create_session(app_name=APP_NAME, user_id=self.user_id)).id
        message = types.Content(role="user", parts=[types.Part(text=text)])
        answer, authors = "", []
        async for event in self.runner.run_async(user_id=self.user_id, session_id=self.session_id, new_message=message):
            if on_event:
                res = on_event(event)
                if inspect.isawaitable(res):
                    await res
            authors.append(event.author)
            if event.content and not event.get_function_calls() and not event.get_function_responses():
                t = "".join(p.text or "" for p in event.content.parts or []).strip()
                if t and event.partial is not True:
                    answer = t  # the last text of the turn: report_renderer, fix_renderer, a guard's refusal, or root's reply
        state = (await self.sessions.get_session(app_name=APP_NAME, user_id=self.user_id, session_id=self.session_id)).state
        names = await self.artifacts.list_artifact_keys(app_name=APP_NAME, user_id=self.user_id, session_id=self.session_id)
        versions = {}
        for n in names:
            versions[n] = len(await self.artifacts.list_versions(app_name=APP_NAME, user_id=self.user_id,
                                                                 session_id=self.session_id, filename=n))
        return {"answer": answer, "state": dict(state), "report": state.get(K.REPORT),
                "fix": state.get(K.FIX_REPORT), "artifacts": versions, "authors": authors}

    async def artifact_text(self, filename: str, version: int | None = None) -> str | None:
        part = await self.artifacts.load_artifact(app_name=APP_NAME, user_id=self.user_id,
                                                  session_id=self.session_id, filename=filename, version=version)
        if part is None:
            return None
        if part.inline_data is not None:
            return part.inline_data.data.decode("utf-8")
        return part.text

    async def close(self) -> None:
        await self.runner.close()


async def run_review(agent: BaseAgent, code: str, fix: bool = False, on_event: Callable[[Event], Any] | None = None) -> dict:
    """Review `code` (and optionally ask for a fix in a second turn). Returns the last turn's result dict."""
    s = ReviewSession(agent)
    try:
        out = await s.ask(f"Please review this code:\n```python\n{code}\n```", on_event)
        if fix:
            out = await s.ask("Please fix the problems you found.", on_event)
        return out
    finally:
        await s.close()
