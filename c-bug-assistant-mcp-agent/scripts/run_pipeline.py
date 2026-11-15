"""Drive the bug assistant from the terminal and print a readable execution trace.

  python scripts/run_pipeline.py --offline                 # scripted LLMs + canned search, real MCP server
  python scripts/run_pipeline.py "Checkout 500s when ..."  # live (needs a free key or Ollama); asks you to approve writes
  python scripts/run_pipeline.py --auto-approve "..."      # live, no prompts (demos only)
  python scripts/run_pipeline.py --list-tools              # connect to the MCP server and list its tools

The trace shows which agent acted, each tool call with its result status, the human-approval prompts, and
the guardrail refusals - which is what to narrate in the video walkthrough.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # optional: GOOGLE_API_KEY, BUGASSIST_PROVIDER, ...

from bug_assistant.guardrails import unwrap  # noqa: E402
from bug_assistant.runner import CONFIRM_CALL, run_once  # noqa: E402


def short(value, n=100):
    s = json.dumps(value, default=str) if not isinstance(value, str) else value
    return s if len(s) <= n else s[: n - 3] + "..."


def print_event(ev) -> None:
    for p in (ev.content.parts if ev.content else None) or []:
        if p.function_call:
            if p.function_call.name == CONFIRM_CALL:
                orig = (p.function_call.args or {}).get("originalFunctionCall") or {}
                print(f"  [{ev.author}] ** PAUSED: approval needed for {orig.get('name')}")
            else:
                print(f"  [{ev.author}] -> {p.function_call.name}({short(p.function_call.args or {}, 110)})")
        elif p.function_response:
            r = p.function_response.response or {}
            if p.function_response.name == CONFIRM_CALL:
                print(f"  [human] approval answer: {r}")
                continue
            data = unwrap(r)
            status = data.get("status") or ("error" if "error" in data else "ok")
            detail = data.get("message") or data.get("error") or data.get("result") or ""
            if "requires confirmation" in str(detail):
                status, detail = "waiting-for-human", ""
            n = data.get("count")
            extra = f" count={n}" if n is not None else ""
            tid = (data.get("ticket") or {}).get("id")
            extra += f" ticket=#{tid}" if tid else ""
            print(f"  [{ev.author}] <- {p.function_response.name}: status={status}{extra} {short(detail, 120)}".rstrip())
        elif p.text:
            print(f"  [{ev.author}] {p.text.strip().replace(chr(10), ' ')[:200]}")


def interactive_approver(tool: str, args: dict) -> bool:
    print(f"\n  APPROVE {tool}?\n  {json.dumps(args, indent=2, default=str)}")
    return input("  approve? [y/N] ").strip().lower() in ("y", "yes")


def auto_approver(tool: str, args: dict) -> bool:
    print(f"  [human] (auto) approved {tool}")
    return True


async def run_scenario(title: str, agent, report: str, approver) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}\n>>> USER: {report}")
    out = await run_once(agent, report, approve=approver, on_event=print_event)
    print(f"\nASSISTANT: {out['answer']}\n")
    print(f"writes={out['writes']}  ticket changes={[(w['tool'], w['ticket_id']) for w in out['write_log']]}"
          f"  refused by policy={len(out['blocked'])}")
    for b in out["blocked"]:
        print(f"  refused {b['tool']}: {b['reason'][:110]}")


async def list_tools() -> None:
    from bug_assistant.pipeline import ticket_toolsets

    read, write = ticket_toolsets()
    for label, ts in (("read", read), ("write (needs approval)", write)):
        tools = await ts.get_tools()
        print(f"{label}: {sorted(t.name for t in tools)}")
        await ts.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", help="the bug report to triage (live mode)")
    ap.add_argument("--offline", action="store_true", help="scripted LLMs + canned web search (no key needed)")
    ap.add_argument("--auto-approve", action="store_true", help="approve every write without asking")
    ap.add_argument("--list-tools", action="store_true", help="connect to the MCP server and list tools")
    args = ap.parse_args()

    if args.list_tools:
        await list_tools()
        return

    if args.offline:
        os.environ["BUGASSIST_DB"] = str(Path(tempfile.mkdtemp(prefix="bugassist_demo_")) / "tickets.db")
        from tests import demo_offline as d

        approver = interactive_approver if args.report == "interactive" else auto_approver
        print("(offline demo database: a fresh temporary SQLite file, seeded with 8 demo tickets)")
        await run_scenario("SCENARIO 1 - a duplicate report", d.duplicate_agent(), d.DUPLICATE_REPORT, approver)
        await run_scenario("SCENARIO 2 - a new bug, researched and filed", d.new_bug_agent(), d.NEW_REPORT, approver)
        return

    if not args.report:
        ap.error("give a bug report, or use --offline / --list-tools")
    from bug_assistant.pipeline import build_root_agent

    await run_scenario("LIVE RUN", build_root_agent(), args.report,
                       auto_approver if args.auto_approve else interactive_approver)


if __name__ == "__main__":
    asyncio.run(main())
