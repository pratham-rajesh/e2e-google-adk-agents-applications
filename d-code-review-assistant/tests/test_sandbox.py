"""The sandbox: what runs, what is refused, what is killed, what the child process can see."""

import os
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_review_assistant.config import K
from code_review_assistant.tools import sandbox
from code_review_assistant.tools.sandbox import docker_command, parse_pytest_output, run_pytest
from code_review_assistant.tools.testing import run_generated_tests

ADD = "def add(a, b):\n    return a + b\n"
T_OK = "from solution import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def test_passing_and_failing_runs_report_counts_and_failures():
    ok = run_pytest(ADD, T_OK)
    assert ok["status"] == "passed" and ok["passed"] == 1 and ok["failed"] == 0 and ok["exit_code"] == 0
    bad = run_pytest(ADD, T_OK + "\n\ndef test_wrong():\n    assert add(1, 1) == 3\n")
    assert bad["status"] == "failed" and (bad["passed"], bad["failed"]) == (1, 1)
    assert bad["failures"] and bad["failures"][0].startswith("FAILED test_solution.py::test_wrong")


def test_import_error_in_tests_is_error_not_pass():
    r = run_pytest(ADD, "from solution import missing\n\n\ndef test_x():\n    pass\n")
    assert r["status"] == "error" and r["passed"] == 0


def test_infinite_loop_is_killed_at_the_timeout():
    t = time.monotonic()
    r = run_pytest("def spin():\n    while True:\n        pass\n", "from solution import spin\n\n\ndef test_s():\n    spin()\n", timeout=2)
    assert r["status"] == "timeout" and "2s" in r["reason"]
    assert time.monotonic() - t < 15


def test_runaway_output_is_bounded(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_SANDBOX_MAX_OUTPUT", "4000")
    r = run_pytest("def noisy():\n    print('x' * 3_000_000)\n    return 0\n",
                   "from solution import noisy\n\n\ndef test_n():\n    noisy()\n    assert False\n")
    assert r["status"] == "failed" and r["output_truncated"] is True and len(r["output"]) < 6000
    assert "1 failed" in r["output"]  # the pytest summary at the end survives the truncation


def test_child_does_not_see_the_parents_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "super-secret-value")
    monkeypatch.setenv("PYTHONPATH", "/somewhere")
    code = "import os\n\n\ndef env():\n    return dict(os.environ)\n"
    tests = ("from solution import env\n\n\ndef test_env():\n    e = env()\n"
             "    assert 'GOOGLE_API_KEY' not in e and 'PYTHONPATH' not in e\n"
             "    assert all('super-secret' not in v for v in e.values())\n")
    assert run_pytest(code, tests)["status"] == "passed"


def test_runs_in_a_throwaway_directory_that_is_removed():
    before = {p for p in Path(tempfile.gettempdir()).glob("codereview_*")}
    marker = Path.cwd() / "sandbox_marker_should_not_exist.txt"
    code = "def w():\n    open('sandbox_marker_should_not_exist.txt', 'w').write('x')\n"
    r = run_pytest(code, "from solution import w\n\n\ndef test_w():\n    w()\n")
    assert r["status"] == "passed"
    assert not marker.exists()
    assert {p for p in Path(tempfile.gettempdir()).glob("codereview_*")} == before


@pytest.mark.parametrize("code, why", [
    ("import socket\n", "socket"),
    ("import subprocess\n", "subprocess"),
    ("import os\n\n\ndef f(x):\n    os.system(x)\n", "os.system"),
    ("def f(x):\n    return eval(x)\n", "eval"),
    ("import shutil\n\n\ndef f(p):\n    shutil.rmtree(p)\n", "shutil"),
])
def test_dangerous_code_is_refused_not_executed(code, why):
    r = run_pytest(code, "from solution import f\n\n\ndef test_f():\n    pass\n")
    assert r["status"] == "refused" and why in r["reason"] and "CODEREVIEW_SANDBOX=docker" in r["reason"]


def test_dangerous_tests_are_refused_too():
    r = run_pytest(ADD, "import subprocess\nfrom solution import add\n\n\ndef test_a():\n    assert add(1, 1) == 2\n")
    assert r["status"] == "refused" and "tests imports subprocess" in r["reason"]


def test_risky_exec_opt_in(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_ALLOW_RISKY_EXEC", "1")
    assert run_pytest("import socket\n\n\ndef f():\n    return 1\n", "from solution import f\n\n\ndef test_f():\n    assert f() == 1\n")["status"] == "passed"


@pytest.mark.parametrize("tests, status", [
    ("", "invalid_tests"),
    ("   \n", "invalid_tests"),
    ("def test_(:\n", "invalid_tests"),
    ("from solution import add\n", "invalid_tests"),       # no test_ functions
])
def test_invalid_test_modules(tests, status):
    assert run_pytest(ADD, tests)["status"] == status


def test_code_with_syntax_error_is_not_run():
    r = run_pytest("def f(:\n", T_OK)
    assert r["status"] == "refused" and "syntax error" in r["reason"]


def test_oversized_tests_rejected(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_MAX_CODE_CHARS", "100")
    assert run_pytest(ADD, T_OK + "# " + "x" * 200)["status"] == "invalid_tests"


def test_off_mode_never_executes(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_SANDBOX", "off")
    r = run_pytest(ADD, T_OK)
    assert r["status"] == "skipped" and r["sandbox"] == "off"


def test_bad_mode_is_a_config_error(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_SANDBOX", "yolo")
    with pytest.raises(ValueError):
        run_pytest(ADD, T_OK)


def test_docker_mode_without_docker_fails_clearly(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    r = run_pytest(ADD, T_OK, mode="docker")
    assert r["status"] == "error" and "docker CLI was not found" in r["reason"]


def test_docker_command_is_locked_down(monkeypatch):
    monkeypatch.setenv("CODEREVIEW_SANDBOX_IMAGE", "my-sandbox:1")
    argv = docker_command("/tmp/w", "cr-sandbox-abc", 20)
    joined = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"] and "--name cr-sandbox-abc" in joined
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "no-new-privileges", "--pids-limit 128",
                 "--user 65534:65534", "-v /tmp/w:/work:ro", "--memory 512m"):
        assert flag in joined, flag
    assert argv[-len(sandbox.PYTEST_ARGS) - 3:][:3] == ["my-sandbox:1", "python", "-I"]
    assert "-e" in argv and not any("KEY" in a or "TOKEN" in a for a in argv)  # no secrets are forwarded


def test_scrubbed_env_has_no_user_variables(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("MY_TOKEN", "y")
    env = sandbox.scrubbed_env("/h")
    assert "OPENAI_API_KEY" not in env and "MY_TOKEN" not in env and env["HOME"] == "/h"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1" and "PYTHONPATH" not in env


@pytest.mark.parametrize("output, code, status, counts", [
    ("..F\nFAILED test_solution.py::test_b - assert 1 == 2\n1 failed, 2 passed in 0.10s\n", 1, "failed", (2, 1, 0)),
    ("...\n3 passed in 0.01s\n", 0, "passed", (3, 0, 0)),
    ("ERROR test_solution.py - ImportError\n1 error in 0.05s\n", 2, "error", (0, 0, 1)),
    ("no tests ran in 0.01s\n", 5, "no_tests", (0, 0, 0)),
    ("Traceback (most recent call last): boom", 3, "error", (0, 0, 0)),
    ("1 passed, 1 skipped in 0.02s\n", 0, "passed", (1, 0, 0)),
])
def test_parse_pytest_output(output, code, status, counts):
    r = parse_pytest_output(output, code)
    assert r["status"] == status and (r["passed"], r["failed"], r["errors"]) == counts


def test_summarize_for_llm_trims_long_output():
    long = {"status": "failed", "output": "a" * 10000}
    assert len(sandbox.summarize_for_llm(long)["output"]) < 3000
    assert sandbox.summarize_for_llm({"status": "passed", "output": "ok"})["output"] == "ok"


async def test_run_generated_tests_tool_stores_state_and_saves_an_artifact():
    saved = {}

    class Ctx:
        state = {K.CODE: ADD}

        async def save_artifact(self, filename, part):
            saved[filename] = part.text

    ctx = Ctx()
    out = await run_generated_tests(T_OK, ctx)
    assert out["status"] == "passed" and ctx.state[K.TEST_RESULT]["status"] == "passed"
    assert ctx.state[K.TESTS] == T_OK and saved["generated_tests.py"] == T_OK
    assert (await run_generated_tests(T_OK, SimpleNamespace(state={})))["status"] == "error"
