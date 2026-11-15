"""`python -m ticket_mcp_server` starts the server on stdio (what McpToolset launches as a subprocess).

stdout carries the MCP protocol, so nothing else may print to it; logs go to stderr.
"""

from .server import create_server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
