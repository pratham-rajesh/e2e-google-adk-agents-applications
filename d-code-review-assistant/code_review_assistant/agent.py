"""Entry point discovered by `adk web` / `adk run` / `adk api_server`."""

from .pipeline import build_root_agent

root_agent = build_root_agent()
