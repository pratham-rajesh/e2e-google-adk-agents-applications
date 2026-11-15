"""A scripted ADK model so the whole agent graph can run offline and deterministically."""

from __future__ import annotations

from typing import AsyncGenerator, Callable, Union

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

Step = Union[str, dict, Callable[[LlmRequest], Union[str, dict]]]


def text(s: str) -> str:
    return s


def call(name: str, **args) -> dict:
    return {"function_call": {"name": name, "args": args}}


class FakeLlm(BaseLlm):
    """Replies with `script[0]`, `script[1]`, ... (the last step repeats once exhausted).

    A step is plain text, `call("tool", **args)` for a function call, or a callable receiving the
    LlmRequest (handy for asserting on the prompt). Every request is kept in `requests`.
    """

    model: str = "gemini-fake"
    script: list = []
    requests: list = []
    _n: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        step = self.script[min(self._n, len(self.script) - 1)]
        self._n += 1
        if callable(step):
            step = step(llm_request)
        if isinstance(step, dict):
            fc = step["function_call"]
            part = types.Part(function_call=types.FunctionCall(name=fc["name"], args=fc["args"]))
        else:
            part = types.Part(text=step)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))

    def system_text(self, index: int = -1) -> str:
        si = self.requests[index].config.system_instruction
        return si if isinstance(si, str) else str(si)
