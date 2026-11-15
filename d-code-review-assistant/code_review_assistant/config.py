"""Environment-driven settings and the session-state keys (all env vars: CODEREVIEW_*)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "code_review_assistant"


class K:
    """Session-state keys. Stages communicate only through these, which is what makes them testable."""

    # intake
    CODE = "code_to_review"            # str: the code under review (original submission)
    INTAKE = "intake"                  # dict: {ok, chars, lines, syntax_ok, syntax_error}
    ROUND = "review_round"             # int: how many reviews this session has run
    # review pipeline
    ANALYSIS = "analysis"              # dict from analyze_code_structure (deterministic)
    STRUCTURE_NOTES = "structure_notes"  # output_key of code_analyzer (LLM commentary)
    STYLE = "style_result"             # dict from check_code_style (deterministic)
    STYLE_NOTES = "style_notes"        # output_key of style_checker
    TESTS = "generated_tests"          # str: pytest module written by the test_runner agent
    TEST_RESULT = "test_result"        # dict from the sandbox
    TEST_NOTES = "test_notes"          # output_key of test_runner
    REPORT_RAW = "report_raw"          # output_key of feedback_synthesizer (JSON text or dict)
    REPORT = "review_report"           # dict: validated ReviewReport after the renderer
    # fix pipeline
    FIXER_OUT = "fixer_output"         # output_key of code_fixer
    FIXED_CODE = "fixed_code"          # str: latest extracted candidate
    FIX_RESULT = "fix_test_result"     # dict: sandbox (or static) validation of the candidate
    FIX_FEEDBACK = "fix_feedback"      # str: what the fixer is told after a failed attempt
    FIX_ITER = "fix_iteration"         # int
    FIX_PASSED = "fix_passed"          # bool
    FIX_NOTES = "fix_notes"            # output_key of fix_synthesizer
    FIX_REPORT = "fix_report"          # dict: final fix outcome

    REVIEW_KEYS = (ANALYSIS, STRUCTURE_NOTES, STYLE, STYLE_NOTES, TESTS, TEST_RESULT, TEST_NOTES, REPORT_RAW, REPORT)
    FIX_KEYS = (FIXER_OUT, FIXED_CODE, FIX_RESULT, FIX_FEEDBACK, FIX_ITER, FIX_PASSED, FIX_NOTES, FIX_REPORT)


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def max_code_chars() -> int:
    """Largest submission accepted (a prompt-size and abuse limit)."""
    return _int("CODEREVIEW_MAX_CODE_CHARS", 20000)


def max_line_length() -> int:
    return _int("CODEREVIEW_MAX_LINE_LENGTH", 100)


def complexity_limit() -> int:
    """Cyclomatic complexity above which a function is flagged."""
    return _int("CODEREVIEW_COMPLEXITY_LIMIT", 10)


def strict_schema() -> bool:
    """Give the synthesizer a hard `output_schema` (Gemini only). Off by default: ADK *raises* when the model's JSON
    is invalid or truncated, which would abort the run; lenient parsing + a deterministic fallback report cannot."""
    return os.getenv("CODEREVIEW_STRICT_SCHEMA", "0").lower() in ("1", "true", "yes")


def fix_max_iterations() -> int:
    return _int("CODEREVIEW_FIX_MAX_ITERATIONS", 3)


# ---- sandbox ---------------------------------------------------------------------------------------------
def sandbox_mode() -> str:
    """subprocess (default) | docker (recommended for anything not your own code) | off (never execute)."""
    mode = os.getenv("CODEREVIEW_SANDBOX", "subprocess").lower()
    if mode not in ("subprocess", "docker", "off"):
        raise ValueError(f"CODEREVIEW_SANDBOX must be subprocess|docker|off, got {mode!r}")
    return mode


def sandbox_timeout() -> float:
    return float(os.getenv("CODEREVIEW_SANDBOX_TIMEOUT", "20"))


def sandbox_output_limit() -> int:
    """Bytes of test output kept (stdout is redirected to a file, so a runaway print cannot fill memory)."""
    return _int("CODEREVIEW_SANDBOX_MAX_OUTPUT", 20000)


def sandbox_image() -> str:
    return os.getenv("CODEREVIEW_SANDBOX_IMAGE", "code-review-sandbox:latest")


def sandbox_memory_mb() -> int:
    return _int("CODEREVIEW_SANDBOX_MEMORY_MB", 512)


def allow_risky_exec() -> bool:
    """In `subprocess` mode, code that touches network / processes / the filesystem destructively is not
    executed unless this is set. (`docker` mode runs it: no network, read-only filesystem.)"""
    return os.getenv("CODEREVIEW_ALLOW_RISKY_EXEC", "0").lower() in ("1", "true", "yes")


# ---- outputs / serving -----------------------------------------------------------------------------------
def artifact_dir() -> str | None:
    """Set to persist artifacts on disk (FileArtifactService). Unset = in memory. For GCS see README."""
    return os.getenv("CODEREVIEW_ARTIFACT_DIR") or None


def server_token() -> str | None:
    return os.getenv("CODEREVIEW_SERVER_TOKEN") or None
