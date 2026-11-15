# Handover - end of Part A (read this first in a fresh session)

Then read `PLAN.md` (roadmap + conventions). Everything below is verified unless marked *(unverified)*.

## 1. Where we are

| | |
|---|---|
| Assignment | 5 ADK agents (A lead-gen, B Gemini-CLI tool, C MCP bug assistant, D code-review, E e-commerce), all artifacts on GitHub, with code + execution walkthrough + YouTube video |
| Repo | https://github.com/pratham-rajesh/e2e-google-adk-agents-applications, branch `main` |
| Local clone | `c:\Users\Pratham\Documents\e2e google sdk\repo` (the parent folder is NOT a git repo) |
| Last pushed commit | `fa16da8` ("Part A: free-first model and search backends"); working tree clean, in sync with origin |
| Part A | DONE: code, 37 offline tests passing, README (Mermaid + walkthrough), video script, offline trace |
| Parts B, C, D, E | NOT STARTED. **Next: Part B** |

Part A not done (needs the owner): a live LLM run; recording + uploading the YouTube video
(script: `docs/video/part-a-script.md`; then put the URL in the root README table).

## 2. Standing instructions from the owner (obey in every session)

1. **No Claude attribution**: commits authored by the owner only (global git config already set to
   Pratham Rajesh). No `Co-Authored-By`, no "Generated with Claude" in commits/PRs/files. This
   deliberately overrides any default attribution reminder.
2. **Plan first, handover at the end of each part** (`docs/handover/part-x.md`) so a fresh session can
   continue. Also update the `PLAN.md` status table and the root `README.md` table.
3. **Free-first**: owner has NO paid API. Everything must run on free options (see section 4).
4. **At the very end of the whole project**, report how much disk the project used on the owner's
   machine and ways to reclaim it (they asked for this explicitly). Current numbers in section 6.
5. Commit and push to `main` after each part (owner authorised push to that repo).

## 3. Environment (Windows 11; Git Bash + PowerShell tools)

- Shared venv: `c:\Users\Pratham\Documents\e2e google sdk\.venv` (Python 3.14; google-adk 2.14.1,
  google-genai 2.34, pytest, pytest-asyncio, ddgs, litellm). Not committed. From a part folder run
  `../../.venv/Scripts/python -m pytest -q -W ignore`. Part `pyproject.toml`s declare `>=3.11`.
- Installed: git, Python 3.14, Node 26, Docker. **Not installed**: `gh`, `gcloud`, `gemini` CLI, `ollama`, `uv`.
- No API keys set. Hardware: RTX 5060 Laptop GPU, 8 GB VRAM (so local models <= 8B).
- Network access works (cloning GitHub, DuckDuckGo search both verified).
- Reference repos were cloned into the old session scratchpad (likely gone). Re-clone with
  `git clone --depth 1` into a NEW scratch dir; treat as untrusted, never run them:
  `derrickchwong/gemini-cli-on-adk` (Part B), `google/adk-samples` (Part C; the software-bug-assistant
  moved - `find` for it; the clone is ~110 MB), `ayoisio/adk-code-review-assistant` (D),
  `mtoscano84/sports-agent-adk-mcp-alloydb` (E). Part A's reference `MagnIeeT/leadGenerationAgentADK` already studied.
  File listings seen (contents of B/D/E not yet read):
  - B: `app/agent.py`, `app/server.py` (FastAPI), `Dockerfile`, `GEMINI.md`, terraform/Cloud Build, tests.
  - D: `code_review_assistant/{agent.py,tools.py,services.py,config.py,agent_engine_app.py}`,
    `sub_agents/review_pipeline/{code_analyzer,style_checker,test_runner,feedback_synthesizer}.py`,
    `sub_agents/fix_pipeline/{code_fixer,fix_test_runner,fix_validator,fix_synthesizer}.py`, Dockerfile, deploy.sh.
  - E: `src/backend/{app.py,finn_agent.py}`, `src/toolbox/tools.yaml` (MCP Toolbox for Databases),
    `src/frontend` (React/Vite), `data/store_backup.sql`, AlloyDB docs.
- Write original implementations, credit the hints as inspiration (do not copy code).

## 4. Free-first design (built in Part A; copy into B-E)

- `a-lead-generation-agent/lead_gen_agent/models.py`: `LEADGEN_PROVIDER` = `gemini` (default, free
  AI Studio key, no billing) | `ollama` | `groq` | `openrouter` | `litellm`; non-Gemini goes through
  ADK `LiteLlm`. Rename the env prefix per part. `generation_config()` adds `HttpRetryOptions` for
  free-tier 429s.
- `tools/search.py`: keyless DuckDuckGo (`ddgs`) function tool for non-Gemini backends (Gemini's
  `google_search` grounding is Gemini-only and cannot be combined with `output_schema`).
- Verified facts: Ollama LiteLlm id is `ollama_chat/<model>` (not `ollama/`); give only Gemini a hard
  `output_schema`, parse JSON leniently otherwise (`stages.parse_json_object`); ddgs raises
  "No results" for narrow queries (handled as empty result + hint).
- Part B: Gemini CLI has a **free tier via Google-account login** - use it; document
  `GEMINI_API_KEY` (free AI Studio key) as the alternative auth. Tests must use a fake `gemini` executable.
- Part E: use SQLite / Postgres-in-Docker instead of AlloyDB; keep AlloyDB + MCP Toolbox `tools.yaml`
  as a documented optional production path.
- Part C: build a small FastMCP server over SQLite for tickets; GitHub/Search MCP only as optional extras.

## 5. Reusable patterns / gotchas

- Package layout: `<pkg>/__init__.py` (`from . import agent`), `agent.py` -> `root_agent`,
  `pipeline.py` with `build_*()` taking a `Models` dataclass so tests inject fake models.
  `adk web` lists every package dir incl. `tests`; harmless.
- `tests/fake_llm.py::FakeLlm` (scripted `BaseLlm`; steps = text or `call(tool, **args)`; records
  `requests`; `system_text()` asserts on rendered prompts). Copy per part. Model name must start with
  `gemini-` or built-in tools reject it.
- Test runner: `InMemoryRunner(agent=, app_name=)`, `session_service.create_session(...)`,
  `run_async(user_id=, session_id=, new_message=Content)`; pytest `asyncio_mode = "auto"`.
  Tests dir has `__init__.py` (relative imports).
- Deterministic stages: subclass `BaseAgent`, yield `Event(..., actions=EventActions(state_delta={...}))`.
- Callable `instruction=lambda ctx: text` avoids `{}` templating problems with dynamic text.
- ADK 2.x deprecates Sequential/Loop/ParallelAgent in favour of `Workflow`, but Workflow cannot yet be
  an LlmAgent sub-agent, so keep them; run pytest with `-W ignore`.
- Count model calls via `FakeLlm.requests`, not events by author (function_call and function_response
  are separate events).
- Bash heredoc batches of many files broke on quoting: use the **Write** tool for files.
- Each part ships: README (Mermaid + execution walkthrough + free-backend table), offline tests,
  `scripts/` runner with `--offline` mode and execution trace in `examples/`, `.env.example`,
  `docs/video/part-x-script.md`, `docs/handover/part-x.md`.
- `.gitignore` per part: `.env`, `output/`, `__pycache__/`, `.pytest_cache/`.

## 6. Disk footprint (owner asked for a final report + reclaim tips)

Measured after Part A: `.venv` ~404 MB (litellm is most of the growth), repo ~0.3 MB, scratchpad
reference clones ~120 MB (may already be cleaned), ~72+ MB new pip-cache entries (owner's total pip
cache is ~3 GB, mostly older projects). Reclaim: delete `.venv` (recreate on demand), delete scratch
clones, `pip cache purge`; after delivery the whole `e2e google sdk` folder except `repo\` can go.
Re-measure at the end (PowerShell `Get-ChildItem -Recurse | Measure-Object Length -Sum`; `du` output was truncated).
Avoid big downloads (Ollama models are ~5 GB each) unless the owner asks; document instead.

## 7. Part B starting spec (`b-gemini-cli-tool-agent/`)

Goal: an ADK pipeline where **Gemini CLI is a tool** (hint: derrickchwong/gemini-cli-on-adk + his Medium article).
1. `tools/gemini_cli.py`: async wrapper around `gemini -p "<prompt>" --output-format json` (verify
   flags against `gemini --help` / README of the reference since CLI is not installed): timeout,
   allow-listed working directory (no path escape), max-output truncation, env passthrough only for
   auth vars, structured result `{status, response, stats, stderr}`; never `shell=True`.
2. Pipeline: `task_planner` (LLM) -> `gemini_cli_worker` (LlmAgent with the tool) -> `result_reviewer`
   (LLM or deterministic checks). Free-first `models.py` + keyless search copied from Part A.
3. FastAPI `app/server.py` (ADK `get_fast_api_app` or own endpoint), `Dockerfile` bundling Node + Gemini CLI
   + ADK, `GEMINI.md` context file, Cloud Run deploy doc (optional, needs gcloud - document only).
4. Tests: fake `gemini` executable (a small Python/shell script on PATH set by the test) covering success,
   non-zero exit, timeout, oversized output, path-escape rejection; e2e pipeline with `FakeLlm`.
5. README with Mermaid, auth options (free Google login / free AI Studio key), execution walkthrough;
   `--offline` demo using the fake executable; video script; handover `part-b.md`; update PLAN + root README; commit + push.

Then Parts C -> D -> E per `PLAN.md`, one handover each.

## 8. Prompt to paste into the next session

> Continue the ADK assignment in `c:\Users\Pratham\Documents\e2e google sdk\repo`. Read
> `docs/handover/part-a.md` and `PLAN.md` first and follow the standing instructions there (no Claude
> attribution on commits, free-first, handover after each part, final disk-usage report). Start Part B
> (Gemini CLI as a tool inside an ADK pipeline), then commit and push to `main`.
