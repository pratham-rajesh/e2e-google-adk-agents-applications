"""The deterministic tools: AST analysis and style rules. No LLM, no execution of the code under review."""

from types import SimpleNamespace

import pytest

from code_review_assistant.config import K
from code_review_assistant.stages import extract_code
from code_review_assistant.tools.analyzer import analyze_code, analyze_code_structure, public_api
from code_review_assistant.tools.style import check_code_style, check_style_code


def rules(code: str) -> set[str]:
    return {f["rule"] for f in analyze_code(code)["findings"]}


def test_structure_functions_classes_imports():
    code = ('import os\nfrom collections import deque\n\n\nclass Box:\n    """A box."""\n\n    def put(self, x):\n        return x\n\n\n'
            'async def go(a, b=2, *, c=3):\n    """Go."""\n    if a and b:\n        return c\n')
    a = analyze_code(code)
    assert a["status"] == "ok"
    assert a["imports"] == ["collections", "os"]
    assert [c["name"] for c in a["classes"]] == ["Box"] and a["classes"][0]["methods"] == ["put"]
    go = next(f for f in a["functions"] if f["name"] == "go")
    assert go["async"] and go["args"] == ["a", "b", "c"] and go["has_docstring"]
    assert go["complexity"] == 3  # 1 + if + the `and`
    assert a["metrics"]["functions"] == 2


def test_complexity_counts_branches_loops_and_comprehensions():
    code = ("def f(xs):\n    total = 0\n    for x in xs:\n        if x > 0 and x < 10:\n            total += x\n"
            "        elif x == 0:\n            continue\n    return [y for y in xs if y]\n")
    # 1 + for + if + and + elif + comprehension(1) + its if
    assert analyze_code(code)["functions"][0]["complexity"] == 7


@pytest.mark.parametrize("snippet, rule", [
    ("eval(x)", "risky-call:eval"),
    ("exec(x)", "risky-call:exec"),
    ("import os\nos.system(x)", "risky-call:os.system"),
    ("import pickle\npickle.loads(x)", "risky-call:pickle.loads"),
    ("import subprocess\nsubprocess.run(x, shell=True)", "risky-call:subprocess-shell"),
    ("import subprocess\nsubprocess.run(['ls'])", "risky-call:subprocess"),
    ("import yaml\nyaml.load(x)", "risky-call:yaml.load"),
    ("import hashlib\nhashlib.md5(x)", "risky-call:hashlib.md5"),
    ("password = 'hunter2hunter2'", "hardcoded-secret"),
    ("class C:\n    api_key = 'abcd1234'", "hardcoded-secret"),
    ("db.execute('SELECT ' + col)", "sql-injection"),
    ("db.execute(f'SELECT {col}')", "sql-injection"),
    ("db.execute('SELECT %s' % col)", "sql-injection"),
    ("db.execute('SELECT {}'.format(col))", "sql-injection"),
    ("try:\n    pass\nexcept:\n    pass", "bare-except"),
    ("try:\n    pass\nexcept Exception:\n    pass", "swallowed-exception"),
    ("def f(a=[]):\n    pass", "mutable-default-arg"),
    ("def f(a=dict()):\n    pass", "mutable-default-arg"),
])
def test_risky_constructs_are_flagged(snippet, rule):
    assert rule in rules(snippet)


@pytest.mark.parametrize("snippet", [
    "import yaml\nyaml.load(x, Loader=yaml.SafeLoader)",
    "db.execute('SELECT * FROM t WHERE a = ?', (a,))",
    "db.execute('SELECT * FROM t')",
    "password_env = 'APP_PASSWORD'",     # the *name* of a variable, not a secret
    "token = os.environ['TOKEN']",
    "def f(a=None):\n    pass",
    "def f(a=(1, 2)):\n    pass",
    "try:\n    pass\nexcept ValueError:\n    pass",
])
def test_safe_look_alikes_are_not_flagged(snippet):
    assert not rules(snippet)


def test_syntax_error_is_a_result_not_an_exception():
    a = analyze_code("def broken(:\n    pass\n")
    assert a["status"] == "syntax_error" and a["syntax_error"]["line"] == 1 and a["findings"] == []


def test_sensitive_imports_listed():
    assert analyze_code("import socket, os\nimport requests")["sensitive_imports"] == ["requests", "socket"]


def test_complexity_limit_is_configurable(monkeypatch):
    code = "def f(a):\n    if a:\n        return 1\n    if a > 2:\n        return 2\n    return 3\n"
    assert "high-complexity" not in rules(code)
    monkeypatch.setenv("CODEREVIEW_COMPLEXITY_LIMIT", "2")
    assert "high-complexity" in rules(code)


def test_public_api_ignores_private_and_nested():
    code = "def a():\n    def inner():\n        pass\n\n\ndef _b():\n    pass\n\n\nclass C:\n    def m(self):\n        pass\n"
    assert public_api(code) == {"a", "C"} and public_api("def (") == set()


def test_analyzer_tool_reads_code_from_state_and_stores_result():
    ctx = SimpleNamespace(state={K.CODE: "eval(x)"})
    out = analyze_code_structure(ctx)
    assert out["findings"][0]["rule"] == "risky-call:eval" and ctx.state[K.ANALYSIS] == out
    assert analyze_code_structure(SimpleNamespace(state={}))["status"] == "error"


# ---- style -------------------------------------------------------------------------------------------------
def test_clean_code_has_no_style_issues():
    code = 'import os\n\n\ndef home():\n    """Home dir."""\n    return os.path.expanduser("~")\n'
    assert check_style_code(code)["issue_count"] == 0


def test_style_rules_fire_with_line_numbers():
    code = ("import os, json\ndef BadName(x):  \n\tif x == None:\n\t\treturn 1\nclass lower:\n    pass\n"
            "y = x == True\n" + "z = '" + "q" * 110 + "'")
    counts = check_style_code(code)["counts"]
    for rule in ("F401", "N802", "N801", "E711", "E712", "W291", "W191", "E501", "D103", "D101", "E302", "W292"):
        assert rule in counts, rule
    assert counts["F401"] == 2  # os and json


def test_style_respects_dunder_all_and_attribute_use():
    code = 'import os\nimport json\n\n__all__ = ["json"]\n\nX = os.path.sep\n'
    assert "F401" not in check_style_code(code)["counts"]


def test_line_length_limit_is_configurable(monkeypatch):
    code = "x = '" + "a" * 50 + "'\n"
    assert "E501" not in check_style_code(code)["counts"]
    monkeypatch.setenv("CODEREVIEW_MAX_LINE_LENGTH", "40")
    assert "E501" in check_style_code(code)["counts"]


def test_style_on_syntax_error_reports_e999():
    assert "E999" in check_style_code("def f(:\n")["counts"]


def test_style_output_is_capped():
    out = check_style_code("\n".join(f"x{i} = 1   " for i in range(200)) + "\n")
    assert out["issue_count"] == 200 and len(out["issues"]) == 60 and out["truncated"]


def test_style_tool_stores_result_in_state():
    ctx = SimpleNamespace(state={K.CODE: "import os\n"})
    assert check_code_style(ctx)["counts"]["F401"] == 1 and K.STYLE in ctx.state


# ---- intake: extracting code from a chat message -------------------------------------------------------------
def test_extract_code_variants():
    assert extract_code("review:\n```python\nx = 1\n```\nthanks") == "x = 1"
    assert extract_code("```\ndef f():\n    pass\n```") == "def f():\n    pass"
    assert extract_code("def f():\n    return 1\n") == "def f():\n    return 1"
    assert extract_code("def broken(:\n    pass") is not None            # reported later as a syntax error
    assert extract_code("hello, can you help me?") is None
    assert extract_code("hello") is None                                  # parses as an expression, is not code
    assert extract_code("") is None
    two = "```python\nx=1\n```\nand also\n```python\ndef f():\n    return 2\n```"
    assert extract_code(two) == "x=1"                                     # the first block wins when prose precedes
