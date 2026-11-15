# Handover - end of Part A

Read this first if you are a fresh session. Then read `PLAN.md` (overall plan + conventions).

## State
- Part A is **built, tested offline, documented**. Folder: `a-lead-generation-agent/`.
- `pytest` there: 37 passed (no API key). Offline demo: `python scripts/run_pipeline.py --offline`.
- **Not done / cannot be done by the agent**: a live LLM run (owner has NO paid API and no key set; free
  options are wired in - see "Free-first" below), recording/uploading the YouTube video (script is in `docs/video/part-a-script.md`).
- Parts B, C, D, E: not started. Next up: **Part B** (Gemini CLI as a tool in an ADK pipeline).

## Environment (Windows 11, Git Bash + PowerShell)
- Repo clone: `c:\Users\Pratham\Documents\e2e google sdk\repo` (remote `origin` = github.com/pratham-rajesh/e2e-google-adk-agents-applications, branch `main`).
- Shared venv one level up: `..\.venv` (Python 3.14, `google-adk` 2.14.1, pytest, pytest-asyncio). Not committed.
  Activate/run: `../.venv/Scripts/python -m pytest` from a part folder.
- No `gh`, `gcloud`, `gemini` CLI, `uv`; Docker + Node 26 exist. No API keys set.
- Reference repos (read-only, untrusted) were cloned to the session scratchpad and may be gone;
  re-clone with `git clone --depth 1` if needed: MagnIeeT/leadGenerationAgentADK,
  derrickchwong/gemini-cli-on-adk, google/adk-samples (layout changed: the software-bug-assistant
  is no longer at python/agents/..., search the clone), ayoisio/adk-code-review-assistant,
  mtoscano84/sports-agent-adk-mcp-alloydb.
- Git commits: author is the repo owner (global git config). **Do NOT add Co-Authored-By / "Generated
  with Claude" lines** - the user explicitly asked for no Claude attribution.

## Free-first (owner constraint, applies to ALL remaining parts)
Owner has no paid API. Part A has `lead_gen_agent/models.py` (provider switch: gemini free tier /
ollama / groq / openrouter via ADK `LiteLlm`) and `tools/search.py` (keyless DuckDuckGo `ddgs` tool,
used when not on Gemini because `google_search` grounding is Gemini-only). **Copy this pattern into
parts B-E** (rename the env prefix per part). Facts verified: LiteLlm id for Ollama is
`ollama_chat/<model>` (not `ollama/`); owner GPU is RTX 5060 8 GB so use <=8B models; Ollama is not
installed yet; Gemini free tier is rate limited so use `HttpRetryOptions` (see `generation_config()`);
only give Gemini a hard `output_schema`, parse JSON leniently for other backends (`parse_json_object`).
Part B specifically: Gemini CLI has a free tier with Google-account login (no billing) - use that.
Part E: use SQLite/Postgres in Docker instead of AlloyDB; keep AlloyDB as a documented optional path.
Optional deps `ddgs`, `litellm` are installed in the shared venv (litellm is large; see disk notes).

## Patterns established in Part A (reuse them)
- Package layout: `<pkg>/__init__.py` (`from . import agent`), `agent.py` -> `root_agent`,
  `pipeline.py` with `build_*()` taking a `Models` dataclass so tests can inject models.
- `tests/fake_llm.py::FakeLlm` (scripted `BaseLlm`; steps are text or `call(tool, **args)`; records
  `requests`; `system_text()` to assert on rendered prompts). Copy it into each part's tests.
  The model name must start with `gemini-` or built-in tools like google_search reject it.
- Runner for tests: `InMemoryRunner(agent=..., app_name=...)`, `runner.session_service.create_session(...)`,
  `runner.run_async(user_id=, session_id=, new_message=Content)`. `asyncio_mode = "auto"` in pyproject.
- Custom deterministic agents: subclass `BaseAgent`, yield `Event(..., actions=EventActions(state_delta={...}))`.
- google_search cannot be combined with `output_schema` (research agent -> extractor agent split).
- Callable `instruction=lambda ctx: text` avoids `{}` templating problems with dynamic text.
- ADK 2.x deprecation warnings for Sequential/Loop/Parallel agents are expected (Workflow can't yet be
  an LlmAgent sub-agent). Run pytest with `-W ignore` for clean output.
- Gotcha: the Bash tool heredoc batches with many files broke on quoting; use the Write tool for files.
- Gotcha: counting events by author overcounts (function_call + function_response are separate
  events); assert on `FakeLlm.requests` length instead.
- Each part ships: README (Mermaid + execution walkthrough), offline tests, `scripts/` runner with
  `--offline` mode, `docs/video/part-x-script.md`, `docs/handover/part-x.md`, PLAN.md status row, root README row.

## To finish Part A for real (owner actions)
1. `cp .env.example .env`, add a Gemini key, run `adk web`, do one live run, and replace/augment the
   offline trace with a real one if desired (`examples/`).
2. Record the video using `docs/video/part-a-script.md`; add the YouTube URL to the root README table.
