"""`gemini_cli`: run Google's Gemini CLI headlessly and hand its answer back to an ADK agent.

Command line built (verified against the CLI's headless docs):

    gemini -p "<fixed instruction>" --output-format json [-m MODEL] [--approval-mode MODE] ...

The task text itself goes through **stdin**, not argv. The docs say `-p` text is appended to stdin, so
this is equivalent, and it means LLM-written text never reaches a shell: on Windows `gemini` is a
`.cmd` shim run by cmd.exe, where `&`, `%` or quotes in an argument would be an injection vector,
and argv length limits don't apply to stdin.

Safety properties (each is covered by tests/test_gemini_cli_tool.py):
  - no `shell=True`; argv list only
  - runs only inside the allow-listed workspace (see workspace.py)
  - hard timeout, process killed on expiry
  - stdout/stderr read with a cap, the final answer truncated to GEMCLI_MAX_OUTPUT_CHARS
  - only allow-listed environment variables are forwarded (config.ENV_PASSTHROUGH)
  - approval mode defaults to `default` (headless: no unconfirmed writes/shell); `yolo` needs an
    explicit second opt-in
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from typing import Any

from google.adk.tools import ToolContext

from .. import config
from .audit import record_call
from .workspace import PathRejected, resolve_in_workspace

# What `-p` carries. The real task arrives on stdin. Deliberately free of shell metacharacters.
PROMPT_STUB = "Carry out the task given on standard input."

EXIT_MEANINGS = {1: "general error or API failure", 42: "invalid prompt or arguments", 53: "turn limit exceeded"}


def build_argv(binary: list[str]) -> list[str]:
    argv = [*binary, "-p", PROMPT_STUB, "--output-format", "json"]
    if model := config.cli_model():
        argv += ["-m", model]
    argv += ["--approval-mode", config.approval_mode()]
    if config.flag("GEMCLI_SANDBOX"):
        argv.append("--sandbox")
    if config.flag("GEMCLI_SKIP_TRUST"):
        argv.append("--skip-trust")
    return argv


def _resolve_binary() -> list[str] | None:
    """Resolve the executable (so Windows finds gemini.cmd). None if it isn't installed."""
    cmd = config.cli_command()
    found = shutil.which(cmd[0])
    if not found:
        return None
    return [found, *cmd[1:]]


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in config.ENV_PASSTHROUGH}


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    """Read to EOF but keep only the first `cap` bytes (the rest is drained so the child can't block)."""
    buf = bytearray()
    over = False
    while chunk := await stream.read(65536):
        if len(buf) < cap:
            buf += chunk[: cap - len(buf)]
        if len(buf) >= cap:
            over = True
    return bytes(buf), over


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n\n[... truncated {len(text) - limit} characters ...]", True


def parse_cli_output(stdout: str) -> dict[str, Any]:
    """`--output-format json` prints {response, stats, error?}. Fall back to raw text if it isn't JSON."""
    stdout = stdout.strip()
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Some versions print log lines before the JSON object; try the last '{' block.
        start = stdout.find("{")
        try:
            data = json.loads(stdout[start:]) if start >= 0 else None
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict) and ("response" in data or "error" in data):
        return {"response": data.get("response") or "", "stats": data.get("stats") or {}, "error": data.get("error")}
    return {"response": stdout, "stats": {}, "error": None}


async def run_gemini_cli(task: str, subdirectory: str = ".", timeout_seconds: int | None = None) -> dict:
    """Core implementation (no ADK types), reused by the tool, the tests and the CLI demo script."""
    started = time.monotonic()
    base: dict[str, Any] = {"subdirectory": subdirectory, "approval_mode": None}

    def done(status: str, **extra: Any) -> dict:
        return {**base, "status": status, "duration_s": round(time.monotonic() - started, 2), **extra}

    if not task or not task.strip():
        return done("rejected", message="task is empty")
    try:
        cwd = resolve_in_workspace(subdirectory)
        base["approval_mode"] = config.approval_mode()
    except (PathRejected, ValueError) as exc:
        return done("rejected", message=str(exc))

    binary = _resolve_binary()
    if binary is None:
        return done(
            "not_installed",
            message=f"'{config.cli_command()[0]}' was not found on PATH. Install it with "
                    "`npm install -g @google/gemini-cli`, or point GEMCLI_BIN at it.",
        )

    limit = max(1, min(int(timeout_seconds or config.timeout_seconds()), config.max_timeout_seconds()))
    cap_chars = config.max_output_chars()
    argv = build_argv(binary)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=_clean_env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return done("error", message=f"could not start Gemini CLI: {exc}")

    async def feed_and_collect():
        assert proc.stdin and proc.stdout and proc.stderr
        try:
            proc.stdin.write(task.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the process exited early; its exit code/stderr will tell the story
        # Raw-bytes cap is a safety net well above the character cap (UTF-8 is up to 4 bytes/char).
        (out, out_over), (err, _) = await asyncio.gather(
            _read_capped(proc.stdout, cap_chars * 8), _read_capped(proc.stderr, 20000)
        )
        await proc.wait()
        return out, out_over, err

    try:
        out, out_over, err = await asyncio.wait_for(feed_and_collect(), timeout=limit)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return done("timeout", message=f"Gemini CLI did not finish within {limit}s and was killed.")

    stderr_text, _ = _truncate(err.decode("utf-8", errors="replace").strip(), 2000)
    parsed = parse_cli_output(out.decode("utf-8", errors="replace"))
    response, truncated = _truncate(parsed["response"], cap_chars)
    truncated = truncated or out_over

    if proc.returncode != 0 or parsed["error"]:
        reason = EXIT_MEANINGS.get(proc.returncode or 0, f"exit code {proc.returncode}")
        err_obj = parsed["error"]
        detail = err_obj.get("message") if isinstance(err_obj, dict) else err_obj
        return done(
            "error", exit_code=proc.returncode, message=f"Gemini CLI failed ({reason})" + (f": {detail}" if detail else ""),
            response=response, stderr=stderr_text,
        )
    return done("ok", exit_code=0, response=response, stats=_slim_stats(parsed["stats"]), truncated=truncated,
                stderr=stderr_text)


def _slim_stats(stats: dict) -> dict:
    """Keep token/latency totals; the full per-tool breakdown is noise for the reviewing LLM."""
    models = stats.get("models") if isinstance(stats, dict) else None
    if not isinstance(models, dict):
        return stats if isinstance(stats, dict) else {}
    slim: dict[str, Any] = {}
    for name, m in models.items():
        tokens = (m or {}).get("tokens", {}) if isinstance(m, dict) else {}
        slim[name] = {"total_tokens": tokens.get("total"), "api": (m or {}).get("api", {})}
    return {"models": slim}


async def gemini_cli(task: str, subdirectory: str = ".", timeout_seconds: int = 0, tool_context: ToolContext = None) -> dict:
    """Run Google's Gemini CLI on a task inside the sandboxed workspace and return its answer.

    Use this for work that benefits from an agentic coding assistant with direct file access:
    explaining a codebase, locating bugs, drafting a test plan, summarising documents. Gemini CLI
    runs read-only unless the operator enabled more. It may take a minute or more.

    Args:
        task: A complete, self-contained instruction for Gemini CLI. It cannot see this conversation,
            so include every detail it needs (what to analyse, desired output format).
        subdirectory: Folder (relative to the workspace root) to run in, e.g. the `subdirectory`
            returned by clone_repository. Defaults to the workspace root.
        timeout_seconds: Optional time limit in seconds; 0 means use the default.

    Returns:
        dict with `status` ("ok", "error", "timeout", "rejected" or "not_installed"). On "ok":
        `response` (the CLI's answer, possibly truncated - see `truncated`) and `stats`. Otherwise
        `message` explains what went wrong. Never invent an answer when status is not "ok".
    """
    result = await run_gemini_cli(task, subdirectory, timeout_seconds or None)
    if tool_context is not None:
        record_call(tool_context.state, task, result)
    return result
