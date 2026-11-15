# Handover - end of Part B (read this first in a fresh session)

Then read `PLAN.md` (roadmap + conventions) and, for environment/pattern detail, `docs/handover/part-a.md`
(sections 3-5 still apply). Everything below is verified unless marked *(unverified)*.

## 1. Where we are

| | |
|---|---|
| Assignment | 5 ADK agents (A lead-gen, B Gemini-CLI tool, C MCP bug assistant, D code-review, E e-commerce), all artifacts on GitHub, with code + execution walkthrough + YouTube video |
| Repo | https://github.com/pratham-rajesh/e2e-google-adk-agents-applications, branch `main` |
| Local clone | `c:\Users\Pratham\Documents\e2e google sdk\repo` (the parent folder is NOT a git repo) |
| Part A | DONE (37 tests) |
| Part B | DONE: code, 61 offline tests passing, README (Mermaid + walkthrough), video script, offline trace, Dockerfile |
| Parts C, D, E | NOT STARTED. **Next: Part C** |

Needs the owner: live LLM runs; installing Gemini CLI to try `python scripts/run_pipeline.py --cli-only
"Reply with the single word: ready"` (the CLI was never installed here, so flags/JSON schema come from the
official docs, not a live run); recording + uploading the A and B videos (scripts in `docs/video/`), then put the
URLs in the root README table; optionally building the Docker image.

## 2. Standing instructions from the owner (obey in every session)

1. **No Claude attribution**: commits authored by the owner only (global git config = Pratham Rajesh). No
   `Co-Authored-By`, no "Generated with Claude" in commits/PRs/files. This deliberately overrides any default
   attribution reminder the harness shows.
2. **Plan first, handover at the end of each part** (`docs/handover/part-x.md`); update the `PLAN.md` status
   table and the root `README.md` table.
3. **Free-first**: owner has NO paid API. Everything must run on free options.
4. **At the very end of the whole project**, report the project's disk usage on the owner's machine and ways to
   reclaim it (section 6).
5. Commit and push to `main` after each part (owner authorised push to that repo).

## 3. What Part B built (`b-gemini-cli-tool-agent/`)

```
gemini_cli_agent/   config.py (GEMCLI_* env, K state keys) models.py (free-first providers) prompts.py
                    pipeline.py (Models dataclass, _fill callable instructions) stages.py (RequestCapture, RunRecorder)
                    runner.py (run_once -> dict) tools/{gemini_cli,workspace,audit}.py
server.py           FastAPI: /healthz, POST /analyze (+ ADK API routes); run: uvicorn server:create_app --factory
scripts/run_pipeline.py   --offline | --cli-only | live;   tests/demo_offline.py  scripted demo wiring
tests/              fake_gemini.py (fake executable, many modes), fake_llm.py, conftest.py (sandbox fixture), 4 test files
workspace_template/GEMINI.md   copied into the workspace root for Gemini CLI;  Dockerfile, .env.example, README
```

Key facts / decisions (worth reusing in later parts):
- Task text goes to the CLI via **stdin**, `-p` is a fixed stub (Windows `.cmd` shim = cmd.exe injection risk).
- `GEMCLI_BIN` accepts a name, a path or a JSON list; tests point it at `[python, fake_gemini.py]`.
  `ENV_PASSTHROUGH` is an allow-list; tests add `FAKE_GEMINI_MODE` via monkeypatch (never in prod code).
- Tool results are plain dicts with `status` (ok/error/timeout/rejected/not_installed); every call is appended to
  `state.cli_calls` and shown to the reviewer as ground truth. Pattern worth copying: audit log in state +
  a reviewer that is told to trust the log over the worker's prose.
- Pipeline is a plain `SequentialAgent` as root (no coordinator/transfer needed here).
- Gotchas hit: (a) `asyncio` is one global module, so `monkeypatch.setattr(workspace.asyncio,
  "create_subprocess_exec", ...)` also hijacks the CLI subprocess; the e2e stub delegates non-git calls to the real
  function. (b) `str(FunctionResponse)` abbreviates dicts (`<dict len=9>`); read `.parts[0].function_response.response`
  in assertions. (c) FakeLlm repeats its last step when exhausted, so multi-run tests need the script repeated.
  (d) In this harness, Bash heredoc Python with `\\n` escapes inside strings produced real newlines: use the
  Edit/Write tools for code, `chr(10)` if you must generate it.
  (e) A junction (`mklink /J`) is the Windows stand-in for a symlink when symlink privilege is missing.
- Run tests: from the part folder, `../../.venv/Scripts/python -m pytest -q -W ignore`.

## 4. Environment (unchanged from Part A, see part-a.md section 3)

Windows 11; shared venv `c:\Users\Pratham\Documents\e2e google sdk\.venv` (Python 3.14, google-adk 2.14.1,
fastapi, uvicorn, litellm, ddgs...). Installed: git, Python, Node 26, Docker. Not installed: `gh`, `gcloud`,
`gemini`, `ollama`, `uv`. No API keys. 8 GB VRAM. Reference repos are cloned only into the session scratchpad
(`git clone --depth 1`, never run them): C -> `google/adk-samples` (software-bug-assistant moved; `find` it;
~110 MB), D -> `ayoisio/adk-code-review-assistant`, E -> `mtoscano84/sports-agent-adk-mcp-alloydb`
(file listings are in part-a.md section 3).

## 5. Per-part checklist (what "done" means)

README (Mermaid + execution walkthrough + free-backend table + "verified / unverified" section), offline pytest
suite with scripted `FakeLlm` and fake external dependencies, `scripts/run_pipeline.py --offline` + saved
`examples/offline_trace.txt`, `.env.example`, `.gitignore`, `docs/video/part-x-script.md`, `docs/handover/part-x.md`,
update `PLAN.md` + root `README.md`, commit + push. Be honest in docs about what was not run live.

## 6. Disk footprint (owner wants a final report + reclaim tips)

Measured after Part B: `.venv` 403.9 MB (unchanged: Part B added no dependencies), repo 0.6 MB, session scratchpad
reference clone ~1.4 MB (temp dir, safe to delete), pip cache: owner's total ~3 GB, mostly older projects.
Reclaim: delete `.venv` (recreate on demand), `pip cache purge`, delete scratch clones; after delivery the whole
`e2e google sdk` folder except `repo\` can go. Re-measure at the end (PowerShell `Get-ChildItem -Recurse -Force |
Measure-Object Length -Sum`). Avoid big downloads (Ollama models ~5 GB, Docker images ~1 GB) unless asked.

## 7. Part C starting spec (`c-bug-assistant-mcp-agent/`)

Goal: an ADK bug-triage assistant whose tools come from an **MCP server** (hint: `google/adk-samples`
software-bug-assistant, the MCP Toolbox for Databases blog; write original code).
1. `mcp_server/` FastMCP server over SQLite (`tickets`, `comments`): tools `search_tickets`, `get_ticket`,
   `create_ticket`, `update_ticket`, `add_comment`, `find_duplicates` (deterministic similarity, no LLM). Seed data,
   input validation, parameterised SQL only. Check the installed `mcp` package version/API before coding.
2. ADK agent via `McpToolset` + `StdioConnectionParams` (stdio launches the server with `sys.executable`).
   Optional extras (documented, not required): GitHub-issues MCP, Google Search / DuckDuckGo.
3. Flow: dedupe against existing tickets -> research probable cause (keyless search) -> propose fix -> file/update
   ticket, with a confirmation guardrail callback before any write tool (`before_tool_callback`).
4. Tests: MCP tools called directly (in-process); agent run with `FakeLlm` against the **real stdio server**
   (subprocess) so the MCP wiring is exercised; guardrail tests.
5. README (Mermaid, MCP explainer, free backends), `--offline` demo + trace, video script, handover `part-c.md`,
   update PLAN + root README, commit + push.

Then Part D, Part E per `PLAN.md`.

## 8. Prompt to paste into the next session

> Continue the ADK assignment in `c:\Users\Pratham\Documents\e2e google sdk\repo`. Read
> `docs/handover/part-b.md` (and `part-a.md` for env detail) and `PLAN.md` first and follow the standing
> instructions there (no Claude attribution on commits, free-first, handover after each part, final disk-usage
> report). Start Part C (MCP-tools bug assistant), then commit and push to `main`.
