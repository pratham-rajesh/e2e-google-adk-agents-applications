"""Run pytest on submitted code in a sandbox.

The code under review is untrusted, and so are the tests an LLM wrote for it. Two sandbox modes:

  subprocess (default, works anywhere)  a fresh interpreter in isolated mode (`-I`), in a throw-away temp dir,
      with a scrubbed environment (no API keys, no HOME), a hard wall-clock timeout that kills the whole process
      tree, bounded output, pytest plugin auto-loading disabled, and POSIX rlimits (CPU, memory, file size,
      processes). It is NOT a security boundary: it does not stop code from reading files the user can read or
      opening sockets. So code that imports network/process modules or makes dangerous calls is *refused*
      (reported, not run) unless CODEREVIEW_ALLOW_RISKY_EXEC=1.
  docker (CODEREVIEW_SANDBOX=docker)    the same run inside a container with no network, a read-only root and
      project mount, dropped capabilities, a non-root user, and memory/CPU/pid limits. This is the mode to use
      for code you did not write; a production deployment should use a managed isolated executor instead.
  off                                   never executes anything (static review only).

Everything returns a plain dict with a `status` from: passed | failed | error | no_tests | timeout |
refused | invalid_tests | skipped.
"""

from __future__ import annotations

import ast
import collections
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .. import config
from .analyzer import analyze_code

CODE_FILE = "solution.py"
TEST_FILE = "test_solution.py"
PYTEST_ARGS = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-rfE", "--disable-warnings", TEST_FILE]
DANGEROUS_SEVERITIES = {"critical", "high"}


# ---- pre-flight ------------------------------------------------------------------------------------------
def validate_inputs(code: str, tests: str) -> dict | None:
    """A result dict if the run must not happen, else None."""
    if not tests or not tests.strip():
        return {"status": "invalid_tests", "reason": "no test code was provided"}
    if len(tests) > config.max_code_chars():
        return {"status": "invalid_tests", "reason": f"test module is larger than {config.max_code_chars()} characters"}
    try:
        ast.parse(tests)
    except SyntaxError as exc:
        return {"status": "invalid_tests", "reason": f"test module has a syntax error at line {exc.lineno}: {exc.msg}"}
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return {"status": "refused", "reason": f"the code under test has a syntax error at line {exc.lineno}: {exc.msg}"}
    if not re.search(r"^\s*(async\s+)?def\s+test_\w+", tests, re.M):
        return {"status": "invalid_tests", "reason": "no test_* functions found in the test module"}
    return None


def risky_reasons(code: str, tests: str) -> list[str]:
    """Why running this in a plain subprocess would be unsafe (empty = fine)."""
    reasons: list[str] = []
    for label, src in (("code", code), ("tests", tests)):
        a = analyze_code(src)
        if a["sensitive_imports"]:
            reasons.append(f"{label} imports {', '.join(a['sensitive_imports'])}")
        reasons += [f"{label}: {f['message']} (line {f['line']})" for f in a["findings"]
                    if f["rule"].startswith("risky-call") and f["severity"] in DANGEROUS_SEVERITIES]
    return reasons


# ---- process plumbing ------------------------------------------------------------------------------------
def scrubbed_env(home: str) -> dict[str, str]:
    """The only environment the child sees: no API keys, no user profile, no PYTHONPATH."""
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "LANG", "LC_ALL")
           if k in os.environ}
    env.update(
        PATH=os.pathsep.join([str(Path(sys.executable).parent)] + (["/usr/bin", "/bin"] if os.name != "nt" else [])),
        HOME=home, USERPROFILE=home, TMP=home, TEMP=home, TMPDIR=home,
        PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONHASHSEED="0", PYTHONNOUSERSITE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", CI="1",
    )
    return env


def _posix_limits(timeout: float) -> Any:  # pragma: no cover - POSIX only, not exercised on the Windows dev box
    def apply() -> None:
        import resource

        cpu = int(timeout) + 2
        mem = config.sandbox_memory_mb() * 1024 * 1024
        for res, val in ((resource.RLIMIT_CPU, cpu), (resource.RLIMIT_AS, mem),
                         (resource.RLIMIT_FSIZE, 10 * 1024 * 1024), (resource.RLIMIT_NPROC, 64),
                         (resource.RLIMIT_CORE, 0)):
            try:
                resource.setrlimit(res, (val, val))
            except (ValueError, OSError):
                pass
    return apply


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:  # pragma: no cover
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


class _BoundedReader(threading.Thread):
    """Drains a pipe keeping only the first and last bytes, so runaway output cannot exhaust memory or disk."""

    def __init__(self, stream: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self.stream, self.half = stream, max(limit // 2, 1)
        self.head = bytearray()
        self.tail: collections.deque[bytes] = collections.deque()
        self.tail_size = 0
        self.total = 0

    def run(self) -> None:
        try:
            while chunk := self.stream.read(8192):
                self.total += len(chunk)
                room = self.half - len(self.head)
                if room > 0:
                    self.head += chunk[:room]
                    chunk = chunk[room:]
                if chunk:
                    self.tail.append(chunk)
                    self.tail_size += len(chunk)
                    while self.tail_size - len(self.tail[0]) >= self.half:
                        self.tail_size -= len(self.tail.popleft())
        except (OSError, ValueError):
            pass

    def text(self) -> tuple[str, bool]:
        tail = b"".join(self.tail)[-self.half:]
        truncated = self.total > len(self.head) + len(tail)
        sep = b"\n...[output truncated]...\n" if truncated else b""
        return (bytes(self.head) + sep + tail).decode("utf-8", "replace"), truncated


def docker_command(workdir: str, name: str, timeout: float) -> list[str]:
    """argv for the docker sandbox. Pure function so the flags are unit-tested without Docker."""
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m,noexec",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
        "--memory", f"{config.sandbox_memory_mb()}m", "--memory-swap", f"{config.sandbox_memory_mb()}m",
        "--cpus", "1", "--pids-limit", "128",
        "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "-e", "HOME=/tmp",
        "-v", f"{workdir}:/work:ro", "-w", "/work",
        config.sandbox_image(), "python", "-I", *PYTEST_ARGS,
    ]


# ---- result parsing --------------------------------------------------------------------------------------
_COUNT = re.compile(r"(\d+) (failed|passed|skipped|errors?|xfailed|xpassed|deselected)")


def parse_pytest_output(output: str, exit_code: int | None) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for line in reversed(output.splitlines()):
        if re.search(r"\bin [\d.]+s\b", line) and _COUNT.search(line):
            for n, word in _COUNT.findall(line):
                key = "errors" if word.startswith("error") else word
                if key in counts:
                    counts[key] = int(n)
            break
    failures = [ln.strip()[:300] for ln in output.splitlines() if ln.startswith(("FAILED ", "ERROR "))][:20]
    if exit_code == 0 and counts["passed"] > 0:
        status = "passed"
    elif exit_code == 5 or "no tests ran" in output:
        status = "no_tests"
    elif counts["failed"] > 0:
        status = "failed"
    else:
        status = "error"  # collection / import errors, interpreter crashes, rlimit kills
    return {"status": status, **counts, "exit_code": exit_code, "failures": failures}


# ---- the runner ------------------------------------------------------------------------------------------
def run_pytest(code: str, tests: str, *, mode: str | None = None, timeout: float | None = None) -> dict[str, Any]:
    """Run `tests` (a pytest module that imports from `solution`) against `code`. Blocking; never raises."""
    mode = mode or config.sandbox_mode()
    timeout = timeout or config.sandbox_timeout()
    base = {"sandbox": mode, "timeout_s": timeout}
    if mode == "off":
        return {**base, "status": "skipped", "reason": "CODEREVIEW_SANDBOX=off: code is never executed"}
    bad = validate_inputs(code, tests)
    if bad:
        return {**base, **bad}
    if mode == "subprocess" and not config.allow_risky_exec():
        reasons = risky_reasons(code, tests)
        if reasons:
            return {**base, "status": "refused",
                    "reason": "not executed in the subprocess sandbox: " + "; ".join(reasons[:4])
                              + ". Use CODEREVIEW_SANDBOX=docker, or set CODEREVIEW_ALLOW_RISKY_EXEC=1 for trusted code."}
    if mode == "docker" and shutil.which("docker") is None:
        return {**base, "status": "error", "reason": "CODEREVIEW_SANDBOX=docker but the docker CLI was not found"}

    root = tempfile.mkdtemp(prefix="codereview_")
    try:
        work = Path(root, "work")
        work.mkdir()
        (work / CODE_FILE).write_text(code, encoding="utf-8")
        (work / TEST_FILE).write_text(tests, encoding="utf-8")
        container = f"cr-sandbox-{uuid.uuid4().hex[:12]}"
        if mode == "docker":
            argv, kwargs = docker_command(str(work), container, timeout), {}
        else:
            argv = [sys.executable, "-I", *PYTEST_ARGS]
            kwargs = {"cwd": str(work), "env": scrubbed_env(root)}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:  # pragma: no cover
                kwargs.update(start_new_session=True, preexec_fn=_posix_limits(timeout))
        started = time.monotonic()
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            return {**base, "status": "error", "reason": f"could not start the sandbox: {exc}"}
        reader = _BoundedReader(proc.stdout, config.sandbox_output_limit())
        reader.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if mode == "docker":
                subprocess.run(["docker", "kill", container], capture_output=True, timeout=15)
            _kill_tree(proc)
            proc.wait(timeout=10)
        reader.join(timeout=5)
        output, truncated = reader.text()
        elapsed = round(time.monotonic() - started, 2)
        if timed_out:
            return {**base, "status": "timeout", "reason": f"killed after {timeout:g}s (infinite loop or very slow code?)",
                    "duration_s": elapsed, "output": output, "output_truncated": truncated, "failures": []}
        parsed = parse_pytest_output(output, proc.returncode)
        return {**base, **parsed, "duration_s": elapsed, "output": output, "output_truncated": truncated}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def summarize_for_llm(result: dict, max_output: int = 2500) -> dict:
    """The result with the (potentially long) raw output trimmed, for returning from a tool to the model."""
    out = dict(result)
    text = out.get("output") or ""
    if len(text) > max_output:
        out["output"] = text[: max_output // 3] + "\n...\n" + text[-(max_output * 2 // 3):]
    return out
