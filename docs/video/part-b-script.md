# Part B video script - Gemini CLI as a Tool in an ADK Pipeline (~11 min)

Recorded by the repo owner. Upload to YouTube (unlisted is fine), then paste the link into the root README table.

| Time | Screen | Say |
|---|---|---|
| 0:00 | Title card + `b-gemini-cli-tool-agent/README.md` | Idea: an agent framework (ADK) orchestrating, and a coding agent (Gemini CLI) doing the file-heavy work as a *tool*. |
| 0:45 | Mermaid diagram | Walk the graph: request capture -> planner -> worker with three tools -> reviewer -> run recorder. Which boxes are LLMs, which are code, and why. |
| 2:15 | `tools/gemini_cli.py` | The core: argv list, no shell. **Task via stdin**, fixed `-p` stub (explain the Windows `.cmd`/cmd.exe injection issue). Timeout + kill, capped reads, scrubbed env, `--approval-mode default`. |
| 4:15 | `tools/workspace.py` | Path jail (`..`, absolute, symlink), clone allow-list (https + hosts, no `ext::`/ssh/credentials). Contrast with the reference's `shell=True`. |
| 5:15 | `pipeline.py`, `prompts.py` | `Models` dataclass, callable instructions (`_fill`) and why braces break ADK templating, reviewer given the call log as ground truth. |
| 6:30 | Terminal: `pytest` | 61 tests, no CLI/key. Open `tests/fake_gemini.py`: a stand-in executable with failure modes; open one injection test. |
| 7:30 | Terminal: `python scripts/run_pipeline.py --offline` | Read the trace aloud: state writes, tool call/result lines, verdict. State clearly that LLM replies and the CLI are scripted here. |
| 8:30 | Terminal: `--cli-only "Reply with the single word: ready"` | Live check of the real CLI install + auth (free Google login or free AI Studio key). *Only if you have installed it; otherwise skip and say so.* |
| 9:00 | Terminal: live pipeline on a small public repo, or `adk web` | Show plan, clone, CLI call, reviewer verdict, `output/run_*.json`. |
| 10:15 | `server.py` + `curl /analyze` | The HTTP surface, bearer token, Dockerfile mention. |
| 10:45 | Wrap-up | Limits (prompt injection in cloned repos, CLI flags verified from docs), next part: MCP-tools bug assistant. |

Pre-flight: `.env` filled in, `workspace/` and `output/` empty, Gemini CLI logged in (`gemini` once),
choose a *small* public repo so the free tier is not exhausted. Fallback if quota fails mid-recording:
`python scripts/run_pipeline.py --offline` (say clearly that replies are scripted).
