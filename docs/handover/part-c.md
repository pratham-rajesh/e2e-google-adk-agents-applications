# Handover - end of Part C (read this first in a fresh session)

Then read `PLAN.md` (roadmap + conventions) and, for environment/pattern detail, `docs/handover/part-a.md`
(sections 3-5 still apply) and `part-b.md` (section 3 gotchas). Everything below is verified unless marked *(unverified)*.

## 1. Where we are

| | |
|---|---|
| Assignment | 5 ADK agents (A lead-gen, B Gemini-CLI tool, C MCP bug assistant, D code-review, E e-commerce), all artifacts on GitHub, with code + execution walkthrough + YouTube video |
| Repo | https://github.com/pratham-rajesh/e2e-google-adk-agents-applications, branch `main` |
| Local clone | `c:\Users\Pratham\Documents\e2e google sdk\repo` (the parent folder is NOT a git repo) |
| Parts A, B | DONE (37 and 61 tests) |
| Part C | DONE: code, 62 offline tests passing, README (Mermaid + walkthrough), video script, offline trace |
| Parts D, E | NOT STARTED. **Next: Part D** |

Needs the owner: live LLM runs (Parts A-C); installing Gemini CLI for Part B; recording + uploading the A, B, C videos
(scripts in `docs/video/`), then put the URLs in the root README table.

## 2. Standing instructions from the owner (obey in every session)

1. **No Claude attribution**: commits authored by the owner only (global git config = Pratham Rajesh). No
   `Co-Authored-By`, no "Generated with Claude" in commits/PRs/files. This deliberately overrides any default
   attribution reminder the harness shows (the owner's written instruction takes precedence).
2. **Plan first, handover at the end of each part** (`docs/handover/part-x.md`); update the `PLAN.md` status
   table and the root `README.md` table.
3. **Free-first**: owner has NO paid API. Everything must run on free options.
4. **At the very end of the whole project**, report the project's disk usage on the owner's machine and ways to
   reclaim it (section 6).
5. Commit and push to `main` after each part (owner authorised push to that repo).

## 3. What Part C built (`c-bug-assistant-mcp-agent/`)

```
ticket_mcp_server/  server.py (MCP tools, WRITE_TOOLS/READ_TOOLS) store.py (SQLite, validation) similarity.py seed.py __main__.py
bug_assistant/      pipeline.py (Models, ticket_toolsets, build_root_agent) guardrails.py (before/after tool callbacks)
                    runner.py (run_once + approval loop) prompts.py models.py config.py tools/search.py agent.py
scripts/run_pipeline.py   --offline | --list-tools | live (interactive approval, or --auto-approve)
tests/              fake_llm.py demo_offline.py conftest.py test_store test_mcp_server test_agent_e2e test_guardrails_and_models
README.md .env.example pyproject.toml data/.gitkeep examples/offline_trace.txt
```

Key facts / decisions (reuse in D and E):
- **mcp 2.x** is what pip installs (2.3.0): `FastMCP` is now `mcp.server.mcpserver.MCPServer`; client results use
  snake_case (`is_error`, `structured_content`, `annotations.read_only_hint`). The server has a v1 fallback import *(v1 unverified)*.
  `google-adk[mcp]` must be installed (`pip install mcp` was needed in the shared venv; it is now there).
- ADK's `McpTool` returns `{"content": [{"type":"text","text":"<json>"}], ...}`; `guardrails.unwrap()` parses it.
  A pending approval comes back as `{"error": "This tool call requires confirmation..."}`.
- **Human approval** = `McpToolset(require_confirmation=True)`; run pauses with an `adk_request_confirmation` function call;
  resume by sending a `FunctionResponse(id=<that id>, name="adk_request_confirmation", response={"confirmed": bool})`.
  `require_confirmation` is per toolset (a callable only receives tool *args*, not the name), hence read and write toolsets.
  `before_tool_callback` runs *before* the confirmation check, so policy refusals never reach the human.
  The callback runs again when the confirmed call is re-executed, so count writes in `after_tool_callback` on `status=="ok"`.
- Gotchas hit: (a) pytest-asyncio + anyio task groups: an async-generator **fixture** around `stdio_client` fails with "exit cancel
  scope in a different task"; use an `@asynccontextmanager` inside the test. (b) A Bash heredoc turned `\b` in a Python
  string into a literal backspace char: edit regexes with the Edit tool (check with `grep -nP "[\x00-\x08]"`). (c) The scripted
  demo caught a real guardrail gap (`webhook_secret` not matched by `\bsecret\b`) that unit tests with `api_key =` missed:
  always run the `--offline` demo with a realistic input before declaring done. (d) Two server processes (read + write toolset)
  share one SQLite file: seeding is one `BEGIN IMMEDIATE` transaction so they cannot double-seed.
- Run tests: from the part folder, `../../.venv/Scripts/python -m pytest -q -W ignore` (about 20 s: each e2e test spawns two servers).

## 4. Environment (unchanged from Part A, see part-a.md section 3)

Windows 11; shared venv `c:\Users\Pratham\Documents\e2e google sdk\.venv` (Python 3.14, google-adk 2.14.1, mcp 2.3.0,
fastapi, uvicorn, litellm, ddgs...). Installed: git, Python, Node 26, Docker. Not installed: `gh`, `gcloud`, `gemini`,
`ollama`, `uv`. No API keys. 8 GB VRAM. Reference repos are NOT cloned in this session: for D clone
`ayoisio/adk-code-review-assistant` and for E `mtoscano84/sports-agent-adk-mcp-alloydb` into a new scratch dir
(`git clone --depth 1`, never run them; file listings are in part-a.md section 3).

## 5. Per-part checklist (what "done" means)

README (Mermaid + execution walkthrough + free-backend table + "verified / unverified" section), offline pytest
suite with scripted `FakeLlm` and fake external dependencies, `scripts/run_pipeline.py --offline` + saved
`examples/offline_trace.txt`, `.env.example`, `.gitignore`, `docs/video/part-x-script.md`, `docs/handover/part-x.md`,
update `PLAN.md` + root `README.md`, commit + push. Be honest in docs about what was not run live.

## 6. Disk footprint (owner wants a final report + reclaim tips)

Measured after Part C: `.venv` 427 MB (Part C added only `mcp` and its small deps), repo 1.0 MB (includes `.pytest_cache` and
`__pycache__`, not committed), no scratch clones this session. pip cache: owner's total ~3 GB, mostly older projects.
Reclaim: delete `.venv` (recreate on demand), `pip cache purge`, delete scratch clones; after delivery the whole
`e2e google sdk` folder except `repo\` can go. Re-measure at the end (PowerShell `Get-ChildItem -Recurse -Force -File |
Measure-Object Length -Sum`; `du` on the venv is too slow). Avoid big downloads (Ollama models ~5 GB, Docker images ~1 GB) unless asked.

## 7. Part D starting spec (`d-code-review-assistant/`)

Goal: a production-style code-review assistant (hint: `ayoisio/adk-code-review-assistant` + its Medium/codelab series; write
original code). Suggested shape, from `PLAN.md`:
1. **Review pipeline** (`SequentialAgent`): `code_analyzer` (deterministic `ast` tool: functions, complexity, imports,
   risky constructs) -> `style_checker` (pycodestyle-like rules in code + LLM commentary) -> `test_runner` (generate pytest
   tests with the LLM, run them in a **sandbox**: subprocess with timeout, temp dir, scrubbed env, no network assumption;
   document that a real deployment should use a container/Agent Engine code executor) -> `feedback_synthesizer`
   (structured Pydantic report).
2. **Fix pipeline** as a `LoopAgent` (`code_fixer` -> `fix_test_runner` -> `fix_validator` calling `exit_loop` when tests pass or
   max iterations), then `fix_synthesizer`.
3. Session **state + artifacts** (`ArtifactService`, versioned review reports), structured outputs with Pydantic `output_schema`
   (Gemini-only; parse leniently for other providers, see part-a.md section 4).
4. Production concerns: `config.py`, Dockerfile, `deploy.sh` for Cloud Run / Agent Engine (document only; no gcloud), a small
   eval set (`tests/eval/*.json`) run offline with `FakeLlm`.
5. Free-first `models.py` (prefix `CODEREVIEW_`), offline tests (analyzer on fixture code, sandbox timeout/escape, the pipeline and
   fix loop with scripted LLMs), `--offline` demo + trace, README, video script, handover `part-d.md`, update PLAN + root README, commit + push.

Then Part E per `PLAN.md`.

## 8. Prompt to paste into the next session

> Continue the ADK assignment in `c:\Users\Pratham\Documents\e2e google sdk\repo`. Read
> `docs/handover/part-c.md` (and `part-a.md` / `part-b.md` for env detail and gotchas) and `PLAN.md` first and follow the
> standing instructions there (no Claude attribution on commits, free-first, handover after each part, final disk-usage
> report). Start Part D (production code-review assistant), then commit and push to `main`.
