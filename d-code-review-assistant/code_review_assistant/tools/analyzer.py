"""Deterministic structural analysis with the `ast` module (no LLM, no execution of the submitted code).

`analyze_code(code)` is a pure function (unit-tested and used by the evals). `analyze_code_structure` is the
ADK tool wrapper: it reads the code from session state, so the model never has to copy a file into a tool
argument, and it writes the result back to state for the later stages and the fix loop.
"""

from __future__ import annotations

import ast
from typing import Any

from google.adk.tools import ToolContext

from .. import config
from ..config import K

# Calls that are dangerous when reachable from untrusted input. rule -> (severity, message)
RISKY_CALLS: dict[str, tuple[str, str]] = {
    "eval": ("critical", "eval() executes arbitrary code"),
    "exec": ("critical", "exec() executes arbitrary code"),
    "compile": ("medium", "compile() of dynamic source"),
    "__import__": ("medium", "dynamic import"),
    "os.system": ("high", "os.system() runs a shell command"),
    "os.popen": ("high", "os.popen() runs a shell command"),
    "pickle.load": ("high", "pickle can execute code while loading untrusted data"),
    "pickle.loads": ("high", "pickle can execute code while loading untrusted data"),
    "marshal.loads": ("high", "marshal can crash or execute code on untrusted data"),
    "shutil.rmtree": ("high", "recursive delete"),
    "os.remove": ("medium", "deletes a file"),
    "os.unlink": ("medium", "deletes a file"),
    "yaml.load": ("high", "yaml.load without SafeLoader can construct arbitrary objects"),
    "hashlib.md5": ("medium", "MD5 is broken for security purposes"),
    "hashlib.sha1": ("medium", "SHA-1 is broken for security purposes"),
    "random.random": ("info", "random is not cryptographically secure"),
}
SUBPROCESS_CALLS = {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
                    "subprocess.Popen"}
# Modules whose mere import means "this code does I/O or process control" (the sandbox refuses to run them
# in subprocess mode; see tools/sandbox.py).
SENSITIVE_IMPORTS = {"socket", "subprocess", "ctypes", "multiprocessing", "http", "urllib", "requests", "ftplib",
                     "smtplib", "telnetlib", "shutil", "pty", "paramiko"}
SECRET_NAMES = ("password", "passwd", "secret", "token", "api_key", "apikey", "private_key", "access_key")
SQL_METHODS = {"execute", "executemany", "executescript", "raw"}


def dotted(node: ast.AST) -> str:
    """`a.b.c` for Name/Attribute chains, '' for anything else (calls, subscripts...)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _complexity(fn: ast.AST) -> int:
    """McCabe cyclomatic complexity: 1 + decision points."""
    total = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)):
            total += 1
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            total += 1 + len(node.ifs)
        elif isinstance(node, ast.match_case):
            total += 1
    return total


def _max_depth(node: ast.AST, depth: int = 0) -> int:
    nest = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
    deepest = depth
    for child in ast.iter_child_nodes(node):
        deepest = max(deepest, _max_depth(child, depth + 1 if isinstance(child, nest) else depth))
    return deepest


def _is_dynamic_string(node: ast.AST) -> bool:
    """f-string with substitutions, '%'/format interpolation, or '+' concatenation with a non-literal."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return isinstance(node.left, (ast.Constant, ast.JoinedStr)) or isinstance(node.right, ast.Constant)
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"


def _finding(rule: str, severity: str, line: int, message: str) -> dict:
    return {"rule": rule, "severity": severity, "line": line, "message": message}


def _scan_risks(tree: ast.AST) -> list[dict]:
    out: list[dict] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name in RISKY_CALLS:
                sev, msg = RISKY_CALLS[name]
                if name == "yaml.load" and any(k.arg == "Loader" for k in node.keywords):
                    continue
                out.append(_finding(f"risky-call:{name}", sev, line, msg))
            if name in SUBPROCESS_CALLS:
                shell = any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                            for k in node.keywords)
                out.append(_finding("risky-call:subprocess-shell" if shell else "risky-call:subprocess",
                                    "high" if shell else "medium", line,
                                    "subprocess with shell=True allows command injection" if shell
                                    else "spawns a process; check the arguments are not user-controlled"))
            if isinstance(node.func, ast.Attribute) and node.func.attr in SQL_METHODS and node.args \
                    and _is_dynamic_string(node.args[0]):
                out.append(_finding("sql-injection", "high", line,
                                    "SQL built with string interpolation; use parameterised queries"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value) >= 4:
                for t in targets:
                    name = (t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else "").lower()
                    if any(s in name for s in SECRET_NAMES) and not name.endswith(("_env", "_name", "_file", "_path")):
                        out.append(_finding("hardcoded-secret", "high", line,
                                            f"'{name}' looks like a credential committed in source; read it from the environment"))
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                out.append(_finding("bare-except", "medium", line, "bare 'except:' also swallows KeyboardInterrupt/SystemExit"))
            elif dotted(node.type) in ("Exception", "BaseException") and all(isinstance(s, ast.Pass) for s in node.body):
                out.append(_finding("swallowed-exception", "medium", line, "exception caught and silently ignored"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(default, ast.Call) and dotted(default.func) in ("list", "dict", "set")):
                    out.append(_finding("mutable-default-arg", "medium", line,
                                        "mutable default argument is shared between calls"))
        elif isinstance(node, ast.Assert):
            pass
    return out


def _imports(tree: ast.AST) -> list[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    return sorted(mods)


def analyze_code(code: str) -> dict[str, Any]:
    """Structure, complexity and risky constructs of `code`. Never raises; syntax errors are reported."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"status": "syntax_error", "syntax_error": {"line": exc.lineno, "message": exc.msg},
                "functions": [], "classes": [], "imports": [], "findings": [], "sensitive_imports": [],
                "metrics": {"lines": len(code.splitlines())}}

    functions, classes = [], []
    limit = config.complexity_limit()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            args = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            functions.append({
                "name": node.name, "line": node.lineno, "args": args, "async": isinstance(node, ast.AsyncFunctionDef),
                "length": end - node.lineno + 1, "complexity": _complexity(node), "max_nesting": _max_depth(node),
                "has_docstring": ast.get_docstring(node) is not None,
                "annotated": node.returns is not None and all(a.annotation for a in node.args.args if a.arg != "self"),
            })
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "line": node.lineno, "has_docstring": ast.get_docstring(node) is not None,
                            "methods": [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]})
    functions.sort(key=lambda f: f["line"])
    findings = _scan_risks(tree)
    for f in functions:
        if f["complexity"] > limit:
            findings.append(_finding("high-complexity", "medium", f["line"],
                                     f"{f['name']}() has cyclomatic complexity {f['complexity']} (limit {limit})"))
        if f["max_nesting"] > 4:
            findings.append(_finding("deep-nesting", "low", f["line"], f"{f['name']}() nests blocks {f['max_nesting']} deep"))
        if f["length"] > 60:
            findings.append(_finding("long-function", "low", f["line"], f"{f['name']}() is {f['length']} lines long"))
    imports = _imports(tree)
    findings.sort(key=lambda x: (x["line"], x["rule"]))
    return {
        "status": "ok",
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "sensitive_imports": sorted(set(imports) & SENSITIVE_IMPORTS),
        "findings": findings,
        "metrics": {
            "lines": len(code.splitlines()), "functions": len(functions), "classes": len(classes),
            "max_complexity": max((f["complexity"] for f in functions), default=0),
        },
    }


def public_api(code: str) -> set[str]:
    """Top-level public function/class names (what the fix loop must not remove)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_")}


def analyze_code_structure(tool_context: ToolContext) -> dict:
    """Analyse the submitted code: functions, classes, complexity, imports and risky constructs.

    The code under review is held in session state, so call this with no arguments.

    Returns:
        dict with `status`, `functions`, `classes`, `imports`, `findings` (rule, severity, line, message)
        and `metrics`. On a syntax error `status` is "syntax_error" with the line and message.
    """
    code = tool_context.state.get(K.CODE)
    if not code:
        return {"status": "error", "message": "No code under review in this session."}
    result = analyze_code(code)
    tool_context.state[K.ANALYSIS] = result
    return result
