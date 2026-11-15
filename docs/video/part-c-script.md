# Part C video script - MCP-Tools Bug Triage Assistant (~11 min)

Recorded by the repo owner. Upload to YouTube (unlisted is fine), then paste the link into the root README table.

| Time | Screen | Say |
|---|---|---|
| 0:00 | Title card + `c-bug-assistant-mcp-agent/README.md` | Idea: a triage agent whose tools are not Python functions but a separate **MCP server**, so the tracker is reusable by any agent framework. Humans approve every write. |
| 0:45 | "What is MCP" section + Mermaid diagram | Server publishes tools; ADK is a client via `McpToolset`; the agent never imports server code. Walk the diagram: read toolset, write toolset, researcher as AgentTool, the guard, the approval gate. |
| 2:15 | `ticket_mcp_server/server.py` | Six tools, read-only vs write annotations, `{"status": "ok" or "error"}` results, `WRITE_TOOLS` defined once. Run `python scripts/run_pipeline.py --list-tools` to show ADK discovering them over stdio. |
| 3:30 | `store.py` + `similarity.py` | Parameterised SQL, validation, atomic seeding (two processes start together). Duplicate detection is deterministic and explains itself with `matched_terms`; say why not embeddings. |
| 4:45 | `pipeline.py` | Two toolsets with `tool_filter`, `require_confirmation` on the write one; `AgentTool` researcher and why (google_search cannot share an agent with other tools). |
| 5:45 | `guardrails.py` | `before_tool_callback` returns a dict to refuse: dedupe-first, near-duplicate, secrets, no closing, write budget. These run before the human is asked. `after_tool_callback` keeps state + audit trail. Mention the real bug the demo caught (`webhook_secret`). |
| 7:00 | Terminal: `pytest` | 62 tests. Open `test_agent_e2e.py`: real stdio server, scripted LLM. Point at "rejected approval leaves the DB untouched" and "refused before approval". |
| 8:00 | Terminal: `python scripts/run_pipeline.py --offline` | Read scenario 1 (duplicate -> comment, approval pause) and scenario 2 (research, secret refused, retry, approval, ticket #9). State clearly that LLM replies and web search are scripted. |
| 9:15 | Live run: `python scripts/run_pipeline.py "..."` or `adk web` | Free Gemini key or Ollama. Paste a report, approve the write at the prompt, show the ticket with `--list-tools`/sqlite. *If you did not get a live run working, skip and say so.* |
| 10:30 | Wrap-up | Limits: real model behaviour is unverified offline; token-overlap similarity; two server processes share SQLite. Next part: production code-review assistant. |

Pre-flight: `.env` filled in, `data/tickets.db` deleted for a clean demo, free-tier quota available. Fallback if the live
run fails: `--offline` (say clearly that replies are scripted).
