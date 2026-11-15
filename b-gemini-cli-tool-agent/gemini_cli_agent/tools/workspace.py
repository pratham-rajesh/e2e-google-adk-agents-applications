"""Workspace sandboxing: path allow-listing, safe `git clone`, directory listing.

The LLM chooses the directory Gemini CLI runs in, so every path it supplies is untrusted.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from .. import config


class PathRejected(ValueError):
    pass


def resolve_in_workspace(subdirectory: str = ".") -> Path:
    """Resolve `subdirectory` under the workspace root, rejecting anything that escapes it.

    Rejects absolute paths, drive letters, `..` segments, NUL bytes and symlinks pointing outside
    (the final path is fully resolved before the containment check).
    """
    root = config.workspace_root()
    rel = (subdirectory or ".").strip()
    if "\x00" in rel:
        raise PathRejected("path contains a NUL byte")
    p = Path(rel)
    if p.is_absolute() or p.drive or rel.startswith(("/", "\\", "~")):
        raise PathRejected(f"absolute paths are not allowed: {subdirectory!r}")
    if ".." in p.parts:
        raise PathRejected(f"'..' is not allowed in paths: {subdirectory!r}")
    target = (root / p).resolve()
    if not target.is_relative_to(root):
        raise PathRejected(f"path escapes the workspace: {subdirectory!r}")
    if not target.is_dir():
        raise PathRejected(f"not a directory inside the workspace: {subdirectory!r}")
    return target


_REPO_PATH = re.compile(r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def parse_repo_url(url: str) -> tuple[str, str, str]:
    """Validate a clone URL and return (clean_url, owner, repo). https only, allow-listed hosts."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise PathRejected("only https:// repository URLs are allowed")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PathRejected("repository URL must not contain credentials, query or fragment")
    host = (parsed.hostname or "").lower()
    if host not in config.git_hosts():
        raise PathRejected(f"host {host!r} is not allow-listed (GEMCLI_GIT_HOSTS={sorted(config.git_hosts())})")
    m = _REPO_PATH.match(parsed.path)
    if not m:
        raise PathRejected("expected a URL like https://github.com/<owner>/<repo>")
    owner, repo = m["owner"], m["repo"]
    if owner[0] in ".-" or repo[0] in ".-":
        raise PathRejected("invalid owner/repo name")
    return f"https://{host}/{owner}/{repo}", owner, repo


async def clone_repository(repo_url: str) -> dict:
    """Shallow-clone a public Git repository into the workspace so Gemini CLI can analyse it.

    Only https URLs on allow-listed hosts (github.com, gitlab.com by default) are accepted.
    Re-using an already cloned repository is free: the existing checkout is returned.

    Args:
        repo_url: e.g. https://github.com/google/adk-samples

    Returns:
        dict with `status` ("ok" or "error"), and on success `subdirectory` - pass this value to
        the gemini_cli tool as its `subdirectory` argument.
    """
    try:
        clean, owner, repo = parse_repo_url(repo_url)
    except PathRejected as exc:
        return {"status": "error", "message": str(exc)}

    sub = Path("repos") / f"{owner}__{repo}"
    dest = config.workspace_root() / sub
    if (dest / ".git").is_dir():
        return {"status": "ok", "subdirectory": sub.as_posix(), "cached": True}

    git = shutil.which("git")
    if not git:
        return {"status": "error", "message": "git is not installed on this machine"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in config.ENV_PASSTHROUGH}
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for credentials
    try:
        proc = await asyncio.create_subprocess_exec(
            git, "clone", "--depth", "1", "--quiet", "--", clean, str(dest),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            shutil.rmtree(dest, ignore_errors=True)
            return {"status": "error", "message": "git clone timed out after 300s"}
    except OSError as exc:
        return {"status": "error", "message": f"could not run git: {exc}"}
    if proc.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        return {"status": "error", "message": f"git clone failed: {err.decode(errors='replace').strip()[:500]}"}
    return {"status": "ok", "subdirectory": sub.as_posix(), "cached": False}


def list_workspace(subdirectory: str = ".") -> dict:
    """List the files and folders directly inside a workspace directory (one level, max 200 entries).

    Args:
        subdirectory: Path relative to the workspace root. Defaults to the root itself.

    Returns:
        dict with `status` and `entries` (names; folders end with "/").
    """
    try:
        target = resolve_in_workspace(subdirectory)
    except PathRejected as exc:
        return {"status": "error", "message": str(exc)}
    names = sorted(
        (p.name + "/" if p.is_dir() else p.name)
        for p in target.iterdir()
        if p.name != ".git"
    )
    return {"status": "ok", "entries": names[:200], "truncated": len(names) > 200}
