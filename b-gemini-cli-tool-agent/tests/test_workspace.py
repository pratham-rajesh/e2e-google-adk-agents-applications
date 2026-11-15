"""Workspace sandboxing and the clone_repository tool (git itself is stubbed, no network)."""

import pytest

from gemini_cli_agent.tools import workspace
from gemini_cli_agent.tools.workspace import PathRejected, parse_repo_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/google/adk-samples", ("https://github.com/google/adk-samples", "google", "adk-samples")),
        ("https://github.com/google/adk-samples.git/", ("https://github.com/google/adk-samples", "google", "adk-samples")),
        ("  https://GitLab.com/a.b/c_d  ", ("https://gitlab.com/a.b/c_d", "a.b", "c_d")),
    ],
)
def test_parse_repo_url_ok(url, expected):
    assert parse_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",                # not https
        "git@github.com:a/b.git",               # ssh
        "file:///etc/passwd",
        "ext::sh -c 'touch /tmp/pwn'",          # git transport helper
        "https://evil.example/a/b",             # host not allow-listed
        "https://github.com.evil.example/a/b",
        "https://user:pw@github.com/a/b",       # credentials
        "https://github.com/a/b?x=1",
        "https://github.com/a",                 # no repo
        "https://github.com/a/b/c",             # too deep
        "https://github.com/../b",
        "https://github.com/-flag/b",
        "--upload-pack=x",
    ],
)
def test_parse_repo_url_rejected(url):
    with pytest.raises(PathRejected):
        parse_repo_url(url)


async def test_clone_rejects_bad_url_without_running_git(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("git must not run")

    monkeypatch.setattr(workspace.asyncio, "create_subprocess_exec", boom)
    r = await workspace.clone_repository("https://evil.example/a/b")
    assert r["status"] == "error"


class _Proc:
    def __init__(self, rc, err=b""):
        self.returncode, self._err = rc, err

    async def communicate(self):
        return b"", self._err

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


async def test_clone_success_and_cache(monkeypatch, sandbox):
    calls = []

    async def fake_exec(*argv, **kw):
        calls.append(argv)
        dest = argv[-1]
        import os

        os.makedirs(os.path.join(dest, ".git"))
        assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0"
        return _Proc(0)

    monkeypatch.setattr(workspace.asyncio, "create_subprocess_exec", fake_exec)
    r = await workspace.clone_repository("https://github.com/google/adk-samples")
    assert r == {"status": "ok", "subdirectory": "repos/google__adk-samples", "cached": False}
    argv = calls[0]
    assert argv[1:6] == ("clone", "--depth", "1", "--quiet", "--") and argv[6] == "https://github.com/google/adk-samples"
    assert (sandbox / "repos" / "google__adk-samples").is_dir()

    again = await workspace.clone_repository("https://github.com/google/adk-samples")
    assert again["cached"] is True and len(calls) == 1


async def test_clone_failure_cleans_up(monkeypatch, sandbox):
    async def fake_exec(*argv, **kw):
        import os

        os.makedirs(argv[-1])
        return _Proc(128, b"fatal: repository not found")

    monkeypatch.setattr(workspace.asyncio, "create_subprocess_exec", fake_exec)
    r = await workspace.clone_repository("https://github.com/nobody/nothing")
    assert r["status"] == "error" and "not found" in r["message"]
    assert not (sandbox / "repos" / "nobody__nothing").exists()


def test_list_workspace(sandbox):
    (sandbox / "proj" / "a.py").write_text("x")
    (sandbox / "proj" / ".git").mkdir()
    assert workspace.list_workspace("proj") == {"status": "ok", "entries": ["a.py"], "truncated": False}
    assert workspace.list_workspace("..")["status"] == "error"


def test_context_file_installed_once(sandbox):
    from gemini_cli_agent import config

    config.workspace_root()
    ctx = sandbox / "GEMINI.md"
    assert "read-only" in ctx.read_text(encoding="utf-8").lower()
    ctx.write_text("custom", encoding="utf-8")
    config.workspace_root()
    assert ctx.read_text(encoding="utf-8") == "custom"  # never overwritten
