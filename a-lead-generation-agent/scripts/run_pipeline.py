"""Drive the lead-generation agent from the terminal and print a readable execution trace.

  python scripts/run_pipeline.py --offline                 # scripted models, no API key needed
  python scripts/run_pipeline.py "Find leads for ..."      # live Gemini + Google Search
  python scripts/run_pipeline.py --yes "Find leads for ..."  # auto-approve the brief (no prompt)

The trace shows which agent produced each event, tool calls, and state keys written - which is
exactly what to narrate in the video walkthrough.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from lead_gen_agent.config import K  # noqa: E402

APP, USER = "lead_gen_cli", "cli-user"


def _print_event(ev) -> None:
    parts = (ev.content.parts if ev.content else None) or []
    for p in parts:
        if p.function_call:
            print(f"  [{ev.author}] -> tool call {p.function_call.name}({dict(p.function_call.args or {})})")
        elif p.function_response:
            print(f"  [{ev.author}] <- tool result {p.function_response.name}")
        elif p.text:
            text = p.text.strip()
            if ev.author in ("LeadGenCoordinator", "ReportComposer"):
                print(f"\n[{ev.author}]\n{text}\n")
            else:
                print(f"  [{ev.author}] {text[:140].replace(chr(10), ' ')}")
    delta = ev.actions.state_delta if ev.actions else None
    if delta:
        print(f"      state += {sorted(delta)}")
    if ev.actions and ev.actions.transfer_to_agent:
        print(f"  [{ev.author}] => transfer to {ev.actions.transfer_to_agent}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="Find leads for my fraud-detection API aimed at fintechs in India and Singapore. I want 5 leads.")
    ap.add_argument("--offline", action="store_true", help="use scripted fake models (no API key)")
    ap.add_argument("--yes", action="store_true", help="auto-approve the brief")
    args = ap.parse_args()

    if args.offline:
        from tests.demo_offline import build_offline_agent

        agent = build_offline_agent()
        args.yes = True
    else:
        from lead_gen_agent.agent import root_agent as agent

    runner = InMemoryRunner(agent=agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=USER)

    message = args.prompt
    while True:
        print(f"\n>>> USER: {message}")
        async for ev in runner.run_async(
            user_id=USER, session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        ):
            _print_event(ev)
        state = (await runner.session_service.get_session(
            app_name=APP, user_id=USER, session_id=session.id)).state
        if state.get(K.REPORT):
            print(f"CSV: {state.get(K.CSV_PATH)}")
            return
        message = "Looks good, go ahead." if args.yes else input("\nyou> ")


if __name__ == "__main__":
    asyncio.run(main())
