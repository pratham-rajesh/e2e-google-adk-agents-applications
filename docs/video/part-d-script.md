# Part D video script - production code-review assistant (about 8-9 minutes)

Record after a live key is set (`.env` with a free `GOOGLE_API_KEY`), or record the offline demo and say so on screen.
Put the YouTube URL in the root README table afterwards.

## 0:00 - Hook (30 s)
Screen: README title. Say: "An AI code reviewer that says 'looks good' is worthless. This one runs the code's tests in a
sandbox, and plain Python code decides what the model is not allowed to get wrong."

## 0:30 - Architecture (90 s)
Screen: the Mermaid diagram in `README.md`.
* Root agent only routes. `review_pipeline`: intake -> AST analyzer -> style checker -> test writer + sandbox -> synthesizer -> renderer.
* `fix_pipeline`: a `LoopAgent` of fixer -> test runner -> validator, guarded so it refuses to run before a review.
* Point at the table "Design rules": state is the only channel, code never goes through tool arguments, facts are decided by code.

## 2:00 - Deterministic tools (60 s)
Terminal: `python scripts/run_pipeline.py --check tests/demo_offline.py` (or any file with problems).
Say: no LLM was involved: complexity, `eval`, `shell=True`, SQL by string formatting, hard-coded keys, mutable defaults.

## 3:00 - Review, offline walkthrough (120 s)
Terminal: `python scripts/run_pipeline.py --offline`, scroll to Scenario 1.
* Narrate each trace line: transfer, intake, tools called with `{}` (code is in state), `status=failed passed=2 failed=2` is a real pytest run in a temp dir.
* Show the report table; say which findings came from the analyzer and which from the model.

## 5:00 - The fix loop (90 s)
Same run, second half. "Attempt 1 fixed one bug, left two others: rejected, and its failure output is fed to the next prompt.
Attempt 2 validated, the loop exits. The loop's exit is code, not a model decision."
Show the diff and the artifact list (`fixed_code.py`, versioned reports).

## 6:30 - Guardrails (90 s)
Scenario 2: lenient reviewer approves `eval` + `shell=True`. Show `status=refused` from the sandbox, the verdict raised to
REQUEST CHANGES, and the "Policy notes" section. Then open `code_review_assistant/tools/sandbox.py` and walk the three modes
(subprocess / docker / off) and the honest line: "a subprocess is not a security boundary, so risky code is refused".

## 8:00 - Tests, evals, deploy (60 s)
`pytest -q` (127 passed), `python scripts/run_eval.py` (11/11), then the folder listing: `Dockerfile`, `deploy/deploy.sh`,
`deploy/agent_engine_app.py`. Say clearly what is not verified live (see README "Verified / not verified").

## Live-run variant (if recorded with a key)
`python scripts/run_pipeline.py path/to/your_module.py --fix`, or `adk web` and paste code into the chat; show the same
stages in the ADK dev UI trace tab.
