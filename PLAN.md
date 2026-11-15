# Assignment 3 - Plan

Five end-to-end Google ADK agent applications, built one "part" at a time. Each part is a
self-contained folder with its own dependencies, tests, README (architecture + execution
walkthrough) and a video script. After every part a handover note is written to
`docs/handover/` so a fresh session can continue without prior context.

## Repository layout

```
.
├── README.md                      # index + how to run any part
├── PLAN.md                        # this file
├── docs/
│   ├── handover/part-{a..e}.md    # state of the work at the end of each part
│   └── video/part-{a..e}-script.md# narration + shot list for the YouTube walkthrough
├── a-lead-generation-agent/       # Part A
├── b-gemini-cli-tool-agent/       # Part B
├── c-bug-assistant-mcp-agent/     # Part C
├── d-code-review-assistant/       # Part D
└── e-ecommerce-agent/             # Part E
```

## Conventions (apply to every part)

- Python 3.11+, `google-adk` (developed against 2.14.x, code only uses APIs that also exist in 1.x).
- Agent package layout that `adk web` / `adk run` discover: `<pkg>/__init__.py` imports `agent`,
  `<pkg>/agent.py` exposes `root_agent`.
- Config through env vars (`.env.example` per part); no secrets committed.
- **Offline test suite per part** (pytest): deterministic tools are unit-tested; the agent graph is
  exercised end to end with a scripted fake `BaseLlm`, so `pytest` passes with no API key.
- **Live run** is documented in each README. **Free-first rule (owner has no paid API):** every part must
  run on a free backend - Gemini API free tier (AI Studio key, no billing), Ollama (local, 8 GB VRAM box),
  or Groq/OpenRouter free tiers via LiteLLM - selected by env var, plus keyless web search (DuckDuckGo).
  Paid/Vertex/AlloyDB paths are optional extras, never required.
- Original implementations. The hint repos/blogs are credited as inspiration; no code is copied.
- Commits are authored by the repo owner only.

## Parts

### Part A - Deep research lead-generation agent  (`a-lead-generation-agent/`)
Pattern: plan -> human approval -> iterative research loop -> enrichment -> scoring -> cited report.
- `LeadGenCoordinator` (LlmAgent) builds a structured brief via tools, user approves (human-in-the-loop),
  then it transfers to the pipeline; a guardrail callback refuses to run without approval.
- `DiscoveryLoop` (LoopAgent): researcher (google_search) -> extractor (schema) -> merger (code) ->
  evaluator that calls `exit_loop` once coverage is good (or max iterations).
- Enrichment (parallel, SSRF-safe page fetch), dynamic ParallelAgent of per-lead signal analysts.
- Deterministic stages: dedupe, ICP-fit scoring, CSV export. `ReportComposer` writes the cited report.
Deliverables: code, tests, README with Mermaid diagram, sample output, video script.

### Part B - Gemini CLI as a tool inside an ADK pipeline  (`b-gemini-cli-tool-agent/`)
- `gemini_cli_tool`: safe wrapper that shells out to `gemini -p ... --output-format json`
  (timeouts, allow-listed working dir, output truncation, structured result).
- ADK pipeline: `task_planner` -> `gemini_cli_worker` (calls the tool) -> `result_reviewer`.
- FastAPI server + Dockerfile (Gemini CLI + ADK in one image, Cloud Run ready), `GEMINI.md` context file.
- Tests use a fake `gemini` executable so no CLI install/auth is needed.

### Part C - MCP-tools bug assistant  (`c-bug-assistant-mcp-agent/`)
- Custom MCP server (FastMCP) exposing a ticket store (SQLite) - search/create/update/comment tools.
- ADK agent consuming it through `McpToolset` (stdio) plus GitHub-issues MCP / Google Search for context.
- Flow: dedupe against existing tickets -> research cause -> propose fix -> file/update ticket.
- Tests: MCP server tools tested directly + agent run against the live stdio server with a fake LLM.

### Part D - Production code-review assistant  (`d-code-review-assistant/`)
- Review pipeline: `code_analyzer` (AST tool) -> `style_checker` -> `test_runner` (sandboxed pytest
  generation/execution) -> `feedback_synthesizer`; fix pipeline as a `LoopAgent` (fix -> validate).
- Session state + artifacts for versioned reports, structured Pydantic outputs.
- Production concerns: config module, Dockerfile, Agent Engine / Cloud Run deploy script, eval set.

### Part E - Production e-commerce agent  (`e-ecommerce-agent/`)
- Storefront catalogue/orders in Postgres-compatible DB (SQLite locally, AlloyDB in prod via
  MCP Toolbox for Databases `tools.yaml`).
- ADK agent served by FastAPI, React/Vite chat frontend, order status, product search, store locator.
- Deployment: Dockerfiles + Cloud Run docs; guardrail callbacks (auth, PII, order-mutation confirmation).

## Cross-cutting deliverables
- Root README linking all parts, with run instructions and a status table.
- Per part: `docs/video/part-x-script.md` (the YouTube walkthrough is recorded by the repo owner;
  the script gives timestamps, commands to run and what to say).
- Final: push to `main` on `pratham-rajesh/e2e-google-adk-agents-applications`.

## Status

| Part | Build | Offline tests | Live run | Video script | Handover |
|------|-------|---------------|----------|--------------|----------|
| A | done | done (37 tests) | not run (no API key available) | done | done |
| B | done | done (61 tests) | not run (no API key, gemini CLI not installed) | done | done |
| C | done | done (62 tests) | not run (no API key available) | done | done |
| D | done | done (127 tests + 11-case eval set) | not run (no API key, docker sandbox not exercised) | done | done |
| E | todo | todo | needs API key (+AlloyDB optional) | todo | todo |
