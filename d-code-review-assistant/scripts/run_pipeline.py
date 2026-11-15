"""Drive the code-review assistant from the terminal and print a readable execution trace.

  python scripts/run_pipeline.py --offline                    # scripted LLMs, real tools/sandbox/fix loop (no key)
  python scripts/run_pipeline.py path/to/module.py            # live review (needs a free key or Ollama)
  python scripts/run_pipeline.py path/to/module.py --fix      # review, then fix + validate in the sandbox
  python scripts/run_pipeline.py --check path/to/module.py    # no LLM at all: AST + style + (optional) sandbox verdict

The trace shows which stage acted, each tool call with its status, the sandbox verdicts and every loop iteration:
this is what to narrate in the video walkthrough.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # optional: GOOGLE_API_KEY, CODEREVIEW_PROVIDER, ...

from code_review_assistant.runner import ReviewSession  # noqa: E402


def short(value, n=110) -> str:
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


def print_event(ev) -> None:
    for p in (ev.content.parts if ev.content else None) or []:
        if p.function_call:
            args = {k: (f"<{len(v)} chars>" if isinstance(v, str) and len(v) > 60 else v) for k, v in (p.function_call.args or {}).items()}
            print(f"  [{ev.author}] -> {p.function_call.name}({short(args)})")
        elif p.function_response:
            r = p.function_response.response or {}
            if p.function_response.name == "transfer_to_agent":
                continue
            bits = [f"status={r['status']}"] if "status" in r else []
            for key in ("passed", "failed", "errors", "issue_count"):
                if key in r:
                    bits.append(f"{key}={r[key]}")
            if r.get("metrics"):
                bits.append(f"findings={len(r.get('findings') or [])} max_complexity={r['metrics'].get('max_complexity')}")
            if r.get("reason"):
                bits.append(f"reason={short(r['reason'], 90)}")
            print(f"  [{ev.author}] <- {p.function_response.name}: {' '.join(bits)}")
        elif p.text and ev.author in ("code_intake", "fix_test_runner", "fix_validator"):
            print(f"  [{ev.author}] {short(p.text.strip(), 150)}")
        elif p.text and ev.author not in ("report_renderer", "fix_renderer"):
            print(f"  [{ev.author}] {short(p.text.strip(), 150)}")


async def scenario(title: str, agent, code: str, fix: bool) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    session = ReviewSession(agent)
    try:
        print(">>> USER: Please review this code (%d lines)" % len(code.splitlines()))
        out = await session.ask(f"Please review this code:\n```python\n{code}\n```", print_event)
        print("\n" + out["answer"])
        rep = out["report"] or {}
        if rep.get("policy_notes"):
            print("(policy notes recorded in state:", "; ".join(rep["policy_notes"]) + ")")
        if fix:
            print("\n>>> USER: Please fix the problems you found.")
            out = await session.ask("Please fix the problems you found.", print_event)
            print("\n" + out["answer"])
        print("ARTIFACTS (name: versions):", out["artifacts"])
    finally:
        await session.close()


def static_check(path: Path) -> None:
    from code_review_assistant.report import floor_verdict
    from code_review_assistant.tools.analyzer import analyze_code
    from code_review_assistant.tools.style import check_style_code

    code = path.read_text(encoding="utf-8")
    state = {"analysis": analyze_code(code), "style_result": check_style_code(code)}
    verdict, reasons = floor_verdict(state)
    print(f"{path}: {state['analysis']['metrics']}")
    for f in state["analysis"]["findings"]:
        print(f"  {f['severity']:8} line {f['line']:>4}  {f['rule']}: {f['message']}")
    print(f"  style: {state['style_result']['counts']}")
    print(f"  evidence-only verdict: {verdict} {reasons}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="Python file to review (live mode)")
    ap.add_argument("--offline", action="store_true", help="scripted LLMs (no key needed); tools and sandbox are real")
    ap.add_argument("--fix", action="store_true", help="after the review, fix and validate")
    ap.add_argument("--check", action="store_true", help="deterministic checks only, no LLM")
    args = ap.parse_args()

    if args.offline:
        from tests import demo_offline as d

        agent, _, _ = d.sample_agent()
        await scenario("SCENARIO 1 - review a buggy module, then fix it (fix loop: attempt 1 rejected, attempt 2 validated)",
                       agent, d.SAMPLE_CODE, fix=True)
        agent, _, _ = d.risky_agent()
        await scenario("SCENARIO 2 - dangerous code: the sandbox refuses to run it and the policy overrules a lenient reviewer",
                       agent, d.RISKY_CODE, fix=False)
        return
    if not args.file:
        ap.error("give a Python file, or use --offline")
    path = Path(args.file)
    if args.check:
        static_check(path)
        return
    from code_review_assistant.pipeline import build_root_agent

    await scenario(f"LIVE REVIEW of {path}", build_root_agent(), path.read_text(encoding="utf-8"), fix=args.fix)


if __name__ == "__main__":
    asyncio.run(main())
