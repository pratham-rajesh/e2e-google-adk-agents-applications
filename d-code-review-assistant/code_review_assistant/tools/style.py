"""Deterministic style rules (a small, readable subset of pycodestyle / pep8-naming / pydocstyle / pyflakes).

Rule ids follow the common ones (E501, W291, N802, D103, F401, ...) so the output reads like a real linter, but
the implementation is self-contained: no extra dependency and it works on code that does not run.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from google.adk.tools import ToolContext

from .. import config
from ..config import K

SNAKE = re.compile(r"^_{0,2}[a-z][a-z0-9_]*_{0,2}$")
CAPS = re.compile(r"^_?[A-Z][A-Za-z0-9]*$")


def _issue(rule: str, line: int, message: str) -> dict:
    return {"rule": rule, "line": line, "message": message}


def _text_rules(code: str) -> list[dict]:
    out: list[dict] = []
    limit = config.max_line_length()
    lines = code.split("\n")
    for i, line in enumerate(lines, start=1):
        if len(line) > limit:
            out.append(_issue("E501", i, f"line too long ({len(line)} > {limit})"))
        if line != line.rstrip():
            out.append(_issue("W291", i, "trailing whitespace"))
        if line.startswith("\t") or re.match(r"^ *\t", line):
            out.append(_issue("W191", i, "indentation contains tabs"))
    if code and not code.endswith("\n"):
        out.append(_issue("W292", len(lines), "no newline at end of file"))
    return out


def _blank_lines_before(lines: list[str], lineno: int) -> int:
    """Blank lines directly above the def (skipping decorators and comments that belong to it)."""
    i = lineno - 2
    while i >= 0 and (lines[i].lstrip().startswith("@") or lines[i].lstrip().startswith("#")):
        i -= 1
    count = 0
    while i >= 0 and lines[i].strip() == "":
        count += 1
        i -= 1
    return count if i >= 0 else 2  # start of file needs none


def _ast_rules(code: str, tree: ast.Module) -> list[dict]:
    out: list[dict] = []
    lines = code.split("\n")
    used: set[str] = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {dotted_root(n) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    exported: set[str] = set()
    for node in tree.body:  # names listed in __all__ count as used
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            exported = {e.value for e in getattr(node.value, "elts", []) if isinstance(e, ast.Constant)}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name != "*" and name not in used and name not in exported:
                    out.append(_issue("F401", node.lineno, f"'{alias.asname or alias.name}' imported but unused"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not SNAKE.match(node.name):
                out.append(_issue("N802", node.lineno, f"function name '{node.name}' should be lowercase_with_underscores"))
            if not node.name.startswith("_") and ast.get_docstring(node) is None:
                out.append(_issue("D103", node.lineno, f"public function '{node.name}' has no docstring"))
        elif isinstance(node, ast.ClassDef):
            if not CAPS.match(node.name):
                out.append(_issue("N801", node.lineno, f"class name '{node.name}' should use CapWords"))
            if not node.name.startswith("_") and ast.get_docstring(node) is None:
                out.append(_issue("D101", node.lineno, f"public class '{node.name}' has no docstring"))
        elif isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant):
                    if right.value is None:
                        out.append(_issue("E711", node.lineno, "comparison to None should use 'is' / 'is not'"))
                    elif right.value is True or right.value is False:
                        out.append(_issue("E712", node.lineno, "comparison to True/False should be 'if x:' or 'is'"))
    for node in tree.body:  # top-level defs need two blank lines before them
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not tree.body[0]:
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if _blank_lines_before(lines, first) < 2:
                out.append(_issue("E302", node.lineno, "expected 2 blank lines before top-level definition"))
    return out


def dotted_root(node: ast.Attribute) -> str:
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else ""


def check_style_code(code: str) -> dict[str, Any]:
    """Pure function: style issues for `code`, sorted by line, with per-rule counts."""
    issues = _text_rules(code)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        issues.append(_issue("E999", exc.lineno or 1, f"syntax error: {exc.msg}"))
    else:
        issues += _ast_rules(code, tree)
    issues.sort(key=lambda i: (i["line"], i["rule"]))
    counts: dict[str, int] = {}
    for i in issues:
        counts[i["rule"]] = counts.get(i["rule"], 0) + 1
    return {"status": "ok", "issue_count": len(issues), "counts": counts, "issues": issues[:60],
            "truncated": len(issues) > 60}


def check_code_style(tool_context: ToolContext) -> dict:
    """Run the style rules on the submitted code (PEP 8 layout, naming, docstrings, unused imports).

    The code under review is held in session state, so call this with no arguments.

    Returns:
        dict with `issue_count`, per-rule `counts` and the first 60 `issues` (rule, line, message).
    """
    code = tool_context.state.get(K.CODE)
    if not code:
        return {"status": "error", "message": "No code under review in this session."}
    result = check_style_code(code)
    tool_context.state[K.STYLE] = result
    return result
