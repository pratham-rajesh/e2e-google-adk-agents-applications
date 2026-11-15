"""The `run_generated_tests` tool: the test-runner agent writes pytest code, this runs it in the sandbox."""

from __future__ import annotations

import asyncio

from google.adk.tools import ToolContext
from google.genai import types

from ..config import K
from . import sandbox


async def run_generated_tests(test_code: str, tool_context: ToolContext) -> dict:
    """Run a pytest module against the submitted code inside the sandbox and return the outcome.

    The submitted code is saved as `solution.py` next to your tests, so import with
    `from solution import some_function`. Use only pytest and the standard library.

    Args:
        test_code: The complete pytest module source (4-10 focused `test_*` functions).

    Returns:
        dict with `status` (passed | failed | error | no_tests | timeout | refused | invalid_tests | skipped),
        `passed`/`failed`/`errors` counts, `failures` (one line each), a trimmed `output` and, when the run
        did not happen, a `reason`.
    """
    code = tool_context.state.get(K.CODE)
    if not code:
        return {"status": "error", "reason": "No code under review in this session."}
    tool_context.state[K.TESTS] = test_code
    result = await asyncio.to_thread(sandbox.run_pytest, code, test_code)
    tool_context.state[K.TEST_RESULT] = result
    try:  # versioned artifact: every review round keeps its own copy of the tests
        await tool_context.save_artifact("generated_tests.py", types.Part(text=test_code))
    except ValueError:  # no artifact service configured (plain Runner without one)
        pass
    return sandbox.summarize_for_llm(result)
