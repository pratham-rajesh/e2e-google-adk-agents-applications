"""Runtime configuration. Everything is overridable with environment variables (prefix GEMCLI_)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path

try:  # optional, only for local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

TEMPLATE_CONTEXT = Path(__file__).resolve().parent.parent / "workspace_template" / "GEMINI.md"
APPROVAL_MODES = ("default", "auto_edit", "plan", "yolo")


def cli_command() -> list[str]:
    """argv prefix used to launch Gemini CLI.

    GEMCLI_BIN may be a program name/path (`gemini`), a JSON list (`["python", "fake.py"]`, used by the
    tests), or a shell-style string. A bare name is resolved with `shutil.which` at call time.
    """
    raw = os.getenv("GEMCLI_BIN", "gemini").strip()
    if raw.startswith("["):
        return [str(x) for x in json.loads(raw)]
    if " " in raw and not Path(raw).exists():
        return shlex.split(raw, posix=(os.name != "nt"))
    return [raw]


def workspace_root() -> Path:
    """The only directory tree Gemini CLI is ever allowed to run in."""
    path = Path(os.getenv("GEMCLI_WORKSPACE", "workspace")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    context = path / "GEMINI.md"  # Gemini CLI loads this file from the working directory and its parents
    if not context.exists() and TEMPLATE_CONTEXT.is_file():
        shutil.copyfile(TEMPLATE_CONTEXT, context)
    return path


def output_dir() -> Path:
    path = Path(os.getenv("GEMCLI_OUTPUT_DIR", "output"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def timeout_seconds() -> int:
    return int(os.getenv("GEMCLI_TIMEOUT", "300"))


def max_timeout_seconds() -> int:
    """Upper bound the LLM may request for a single call."""
    return int(os.getenv("GEMCLI_MAX_TIMEOUT", "900"))


def max_output_chars() -> int:
    return int(os.getenv("GEMCLI_MAX_OUTPUT_CHARS", "20000"))


def approval_mode() -> str:
    """How much Gemini CLI may do on its own. `default` = read-only in headless mode (anything that
    needs confirmation is denied). `yolo` auto-approves shell/file writes and must be unlocked
    explicitly with GEMCLI_ALLOW_YOLO=1."""
    mode = os.getenv("GEMCLI_APPROVAL_MODE", "default").lower()
    if mode not in APPROVAL_MODES:
        raise ValueError(f"GEMCLI_APPROVAL_MODE must be one of {APPROVAL_MODES}, got {mode!r}")
    if mode == "yolo" and os.getenv("GEMCLI_ALLOW_YOLO") != "1":
        raise ValueError("GEMCLI_APPROVAL_MODE=yolo also requires GEMCLI_ALLOW_YOLO=1")
    return mode


def cli_model() -> str | None:
    """Model for Gemini CLI itself (`gemini -m`). Unset = the CLI's own default (auto)."""
    return os.getenv("GEMCLI_CLI_MODEL") or None


def flag(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes")


def git_hosts() -> set[str]:
    return {h.strip().lower() for h in os.getenv("GEMCLI_GIT_HOSTS", "github.com,gitlab.com").split(",") if h.strip()}


# Environment variables forwarded to the Gemini CLI subprocess. Anything else (cloud tokens,
# other API keys, ...) is deliberately NOT passed through.
ENV_PASSTHROUGH = (
    "PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "WINDIR",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "TERM", "NO_COLOR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    # auth options for Gemini CLI itself
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS", "GEMINI_CLI_HOME",
)


# State keys shared between agents. Centralised so typos fail loudly in tests.
class K:
    REQUEST = "user_request"      # the original user task (set by the pipeline's first stage)
    PLAN = "task_plan"            # planner output
    RESULT = "worker_result"      # worker's final write-up of what Gemini CLI returned
    CLI_CALLS = "cli_calls"       # list[dict] audit log appended by the gemini_cli tool
    REVIEW = "review"             # reviewer verdict
    RUN_FILE = "run_record_path"  # JSON audit record written at the end
