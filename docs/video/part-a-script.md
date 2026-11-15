# Part A video script - Deep Research Lead-Generation Agent (~12 min)

Recorded by the repo owner. Upload to YouTube (unlisted is fine), then paste the link into the
root README "Videos" table.

| Time | Screen | Say |
|---|---|---|
| 0:00 | Title card + repo README | What we are building: an ADK agent that researches B2B leads like an analyst would - plan, approve, iterate, score, cite. |
| 0:45 | `README.md` Mermaid diagram | Walk the graph top to bottom: coordinator (human in the loop) -> pipeline -> discovery loop -> enrichment -> parallel signal research -> scoring -> report. Mention which boxes are LLMs and which are plain code, and why. |
| 2:30 | `lead_gen_agent/pipeline.py` | `Models` dataclass, `LoopAgent` with researcher/extractor/merger/evaluator, `before_agent_callback` guardrail. |
| 4:00 | `stages.py` | Show `LeadMergerAgent` and the `state_delta` pattern; show `SignalResearchAgent` building a `ParallelAgent` at run time and why instructions are callables. |
| 5:30 | `tools/web.py`, `tools/scoring.py` | SSRF protection for LLM-supplied URLs; scoring weights. |
| 6:30 | Terminal: `pytest` | 22 tests, no API key, scripted `FakeLlm` drives the real ADK runner. |
| 7:30 | Terminal: `adk web` (live key) | Prompt: *Find leads for my fraud-detection API aimed at fintechs in India and Singapore. 5 leads.* Show the brief, edit one field, approve. |
| 9:00 | ADK web Trace tab | Point at loop rounds, the gaps feeding round 2, the parallel analysts, `exit_loop`. |
| 11:00 | Final report + CSV in `output/` | Ranked table, citations, caveats. |
| 11:45 | Wrap-up | Limitations (live quality depends on search; verify contacts), next part: Gemini CLI as a tool. |

Pre-flight: `.env` with a key, `output/` empty, `adk web` started from `a-lead-generation-agent/`.
Fallback if the key/quota fails mid-recording: `python scripts/run_pipeline.py --offline` (say clearly
that the replies are scripted).
