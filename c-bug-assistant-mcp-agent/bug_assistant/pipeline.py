"""Builds the agent graph.

    bug_triage_assistant  (LlmAgent, root)
      |- ticket READ tools   <- McpToolset  ──stdio──> ticket_mcp_server (subprocess)
      |- ticket WRITE tools  <- McpToolset  ──stdio──> ticket_mcp_server (subprocess, require_confirmation)
      |- cause_researcher    <- AgentTool(LlmAgent + web search)
      callbacks: before_tool_callback = policy guard, after_tool_callback = dedupe state + audit log

Why two toolsets over the same server? `require_confirmation` is a per-toolset switch, so the read-only
tools (no approval needed) and the write tools (human approval required) are separate toolsets, selected
with `tool_filter`. Both use the same SQLite file, which handles two connections fine.

Why is the researcher an AgentTool? Gemini's built-in `google_search` cannot be combined with other
tools in one agent. Wrapping the searcher as its own agent and exposing it as a tool sidesteps that and
keeps the long search transcript out of the triage agent's context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

from ticket_mcp_server.server import READ_TOOLS, WRITE_TOOLS

from . import config, models, prompts
from .config import K
from .guardrails import guard_tool_call, record_tool_result
from .tools.search import search_tools


@dataclass
class Models:
    triage: Any
    researcher: Any

    @classmethod
    def from_env(cls) -> "Models":
        return cls(triage=models.make_model("triage"), researcher=models.make_model("research"))


def ticket_toolsets(*, require_confirmation: bool | None = None) -> tuple[McpToolset, McpToolset]:
    """(read toolset, write toolset). Each launches `python -m ticket_mcp_server` over stdio."""
    command, args = config.server_command()

    def params() -> StdioConnectionParams:
        return StdioConnectionParams(
            server_params=StdioServerParameters(command=command, args=args, env=config.server_env()),
            timeout=30.0,  # first start imports the MCP SDK; the default 5s is tight on a cold Windows box
        )

    confirm = config.require_confirmation() if require_confirmation is None else require_confirmation
    read = McpToolset(connection_params=params(), tool_filter=list(READ_TOOLS))
    write = McpToolset(connection_params=params(), tool_filter=list(WRITE_TOOLS), require_confirmation=confirm)
    return read, write


def build_researcher(m: Models, search: list | None = None) -> LlmAgent:
    return LlmAgent(
        name="cause_researcher",
        model=m.researcher,
        description=(
            "Searches the web for the probable root cause and proven fixes of a bug. Input: the symptom, "
            "exact error text and the technology involved. Output: cause, fixes and source URLs."
        ),
        instruction=prompts.RESEARCHER,
        tools=search if search is not None else search_tools(),
        generate_content_config=models.generation_config(),
        output_key=K.RESEARCH,
    )


def build_root_agent(m: Models | None = None, toolsets: tuple | None = None, search: list | None = None) -> LlmAgent:
    """`m`, `toolsets` and `search` can be injected (tests and the offline demo do)."""
    m = m or Models.from_env()
    read, write = toolsets or ticket_toolsets()
    return LlmAgent(
        name="bug_triage_assistant",
        model=m.triage,
        description="Triages bug reports against a ticket tracker (MCP) and files well-researched tickets.",
        instruction=prompts.TRIAGE,
        tools=[read, write, AgentTool(agent=build_researcher(m, search))],
        before_tool_callback=guard_tool_call,
        after_tool_callback=record_tool_result,
        generate_content_config=models.generation_config(),
    )
