"""Drive the Gemini-CLI pipeline from the terminal and print a readable execution trace.

  python scripts/run_pipeline.py --offline                       # scripted LLMs + fake gemini, no key/CLI
  python scripts/run_pipeline.py "Explain https://github.com/google/adk-samples"   # live
  python scripts/run_pipeline.py --cli-only "say hello"          # just the tool: checks CLI install + auth

The trace shows which agent produced each event, each tool call with its result status, and the
state keys written - which is what to narrate in the video walkthrough.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gemini_cli_agent.runner import run_once  # noqa: E402
from gemini_cli_agent.tools.gemini_cli import run_gemini_cli  # noqa: E402


def print_event(ev) -> None:
    parts = (ev.content.parts if ev.content else None) or []
    for p in parts:
        if p.function_call:
            args = {k: (v[:90] + "..." if isinstance(v, str) and len(v) > 90 else v) for k, v in (p.function_call.args or {}).items()}
            print(f"  [{ev.author}] -> tool call {p.function_call.name}({args})")
        elif p.function_response:
            r = p.function_response.response or {}
            extra = f" ({r['message']})" if r.get("message") else ""
            print(f"  [{ev.author}] <- {p.function_response.name}: status={r.get('status')}{extra}")
        elif p.text:
            first = p.text.strip().replace("\n", " ")
            print(f"  [{ev.author}] {first[:160]}")
    delta = ev.actions.state_delta if ev.actions else None
    if delta:
        print(f"      state += {sorted(delta)}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="Give me an inventory of the files in the sample project.")
    ap.add_argument("--offline", action="store_true", help="scripted LLMs and a fake `gemini` (no key, no CLI)")
    ap.add_argument("--cli-only", action="store_true", help="call only the gemini_cli tool with PROMPT")
    args = ap.parse_args()

    if args.cli_only:
        print(json.dumps(await run_gemini_cli(args.prompt), indent=2))
        return

    if args.offline:
        from tests.demo_offline import build_offline_agent

        agent = build_offline_agent()
    else:
        from gemini_cli_agent.pipeline import build_pipeline

        agent = build_pipeline()

    print(f">>> USER: {args.prompt}")
    out = await run_once(agent, args.prompt, user_id="cli-user", on_event=print_event)
    print(f"\nVERDICT: {out['verdict']}\n\n{out['answer']}\n")
    print(f"Gemini CLI calls: {len(out['cli_calls'])}   run record: {out['run_record']}")


if __name__ == "__main__":
    asyncio.run(main())
