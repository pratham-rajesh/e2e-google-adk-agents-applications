"""The gemini_cli tool against a fake `gemini` executable: success path and every failure mode."""

import os
import sys

import pytest

from gemini_cli_agent import config
from gemini_cli_agent.tools.gemini_cli import build_argv, parse_cli_output, run_gemini_cli


async def test_success_json(monkeypatch):
    r = await run_gemini_cli("Explain the project.", "proj")
    assert r["status"] == "ok" and r["exit_code"] == 0
    assert "FAKE GEMINI ANSWER" in r["response"]
    assert r["stats"]["models"]["gemini-fake"]["total_tokens"] == 42
    assert not r["truncated"]


async def test_task_goes_through_stdin_not_argv():
    nasty = 'list files & del /q * | echo "%PATH%" $(rm -rf /) `x`'
    r = await run_gemini_cli(nasty)
    assert f"task: {nasty}" in r["response"]          # arrived intact on stdin
    argv_line = next(l for l in r["response"].splitlines() if l.startswith("argv:"))
    assert nasty not in argv_line and "del /q" not in argv_line


async def test_runs_in_requested_subdirectory():
    r = await run_gemini_cli("x", "proj")
    assert "cwd: proj" in r["response"]


async def test_default_flags():
    argv = build_argv(["gemini"])
    assert argv[:1] == ["gemini"] and argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--approval-mode") + 1] == "default"
    assert "-m" not in argv and "--sandbox" not in argv and "--skip-trust" not in argv


async def test_optional_flags(monkeypatch):
    monkeypatch.setenv("GEMCLI_CLI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMCLI_SANDBOX", "1")
    monkeypatch.setenv("GEMCLI_SKIP_TRUST", "true")
    monkeypatch.setenv("GEMCLI_APPROVAL_MODE", "plan")
    argv = build_argv(["gemini"])
    assert argv[argv.index("-m") + 1] == "gemini-2.5-flash"
    assert "--sandbox" in argv and "--skip-trust" in argv
    assert argv[argv.index("--approval-mode") + 1] == "plan"


async def test_yolo_needs_second_opt_in(monkeypatch):
    monkeypatch.setenv("GEMCLI_APPROVAL_MODE", "yolo")
    r = await run_gemini_cli("x")
    assert r["status"] == "rejected" and "GEMCLI_ALLOW_YOLO" in r["message"]
    monkeypatch.setenv("GEMCLI_ALLOW_YOLO", "1")
    assert (await run_gemini_cli("x"))["status"] == "ok"


@pytest.mark.parametrize("bad", ["..", "../x", "proj/../..", "/etc", "C:\\Windows", "~", "nope", "\\\\host\\share"])
async def test_path_escape_rejected(bad):
    r = await run_gemini_cli("x", bad)
    assert r["status"] == "rejected", bad


async def test_symlink_escape_rejected(sandbox, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = sandbox / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlinks not permitted on this machine")
        import subprocess  # Windows without symlink privilege: a directory junction escapes the same way

        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    assert (await run_gemini_cli("x", "link"))["status"] == "rejected"


async def test_empty_task_rejected():
    assert (await run_gemini_cli("   "))["status"] == "rejected"


async def test_secrets_not_forwarded():
    r = await run_gemini_cli("x")
    assert "secret_visible: False" in r["response"]
    assert os.environ["SUPER_SECRET_TOKEN"]  # it IS set in our process


async def test_non_zero_exit(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "crash")
    r = await run_gemini_cli("x")
    assert r["status"] == "error" and r["exit_code"] == 1 and "boom" in r["stderr"]


async def test_exit_42_is_explained(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "exit42")
    r = await run_gemini_cli("x")
    assert r["status"] == "error" and "invalid prompt or arguments" in r["message"]


async def test_api_error_json(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "apierror")
    r = await run_gemini_cli("x")
    assert r["status"] == "error" and "quota exceeded" in r["message"]


async def test_timeout_kills_process(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "hang")
    r = await run_gemini_cli("x", timeout_seconds=1)
    assert r["status"] == "timeout" and r["duration_s"] < 10


async def test_requested_timeout_is_capped(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "hang")
    monkeypatch.setenv("GEMCLI_MAX_TIMEOUT", "1")
    r = await run_gemini_cli("x", timeout_seconds=3600)
    assert r["status"] == "timeout" and r["duration_s"] < 10


async def test_oversized_output_truncated(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "huge")
    monkeypatch.setenv("GEMCLI_MAX_OUTPUT_CHARS", "1000")
    r = await run_gemini_cli("x")
    assert r["status"] == "ok" and r["truncated"] is True
    assert len(r["response"]) < 1200 and "truncated" in r["response"]


async def test_plain_text_fallback(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "plain")
    r = await run_gemini_cli("x")
    assert r["status"] == "ok" and r["response"] == "just some plain text, not JSON"


async def test_log_noise_before_json(monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "noisy")
    r = await run_gemini_cli("x")
    assert r["status"] == "ok" and r["response"] == "answer after log noise"


async def test_not_installed(monkeypatch):
    monkeypatch.setenv("GEMCLI_BIN", "definitely-not-a-real-binary-xyz")
    r = await run_gemini_cli("x")
    assert r["status"] == "not_installed" and "npm install -g @google/gemini-cli" in r["message"]


async def test_concurrent_calls_do_not_interfere(monkeypatch):
    import asyncio

    monkeypatch.setenv("FAKE_GEMINI_MODE", "sleepy")
    results = await asyncio.gather(*(run_gemini_cli(f"task-{i}", "proj") for i in range(4)))
    for i, r in enumerate(results):
        assert r["status"] == "ok" and f"task: task-{i}" in r["response"]


def test_parse_output_variants():
    assert parse_cli_output('{"response": "hi", "stats": {"a": 1}}')["response"] == "hi"
    assert parse_cli_output("not json")["response"] == "not json"
    assert parse_cli_output('{"unrelated": 1}')["response"] == '{"unrelated": 1}'
    assert parse_cli_output('{"error": {"message": "m"}}')["error"] == {"message": "m"}


def test_cli_command_parsing(monkeypatch):
    monkeypatch.setenv("GEMCLI_BIN", f'["{sys.executable.replace(chr(92), "/")}", "a b.py"]')
    assert config.cli_command()[1] == "a b.py"
    monkeypatch.setenv("GEMCLI_BIN", "gemini")
    assert config.cli_command() == ["gemini"]
