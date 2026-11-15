"""Scripted LLMs for `scripts/run_pipeline.py --offline` and the end-to-end tests.

Only the model replies are stand-ins. ADK's runner, session state, artifact service, the routing transfer, every
deterministic stage, the AST/style tools, the real sandbox (a pytest subprocess) and the fix loop all run for real.

The sample module has four planted problems: a hard-coded credential, SQL built by string formatting, a mutable
default argument, and an average() that divides by zero for an empty list.
"""

from code_review_assistant.pipeline import Models, build_root_agent

from .fake_llm import FakeLlm, call

SAMPLE_CODE = '''import json

API_KEY = "demo-not-a-real-key-4f9a2c71"


def average(numbers):
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)


def find_user(db, name):
    cur = db.execute("SELECT * FROM users WHERE name = '%s'" % name)
    return cur.fetchone()


def add_item(item, items=[]):
    items.append(item)
    return items
'''

TESTS = '''import sqlite3

from solution import add_item, average, find_user


def test_average_of_numbers():
    assert average([2, 4, 6]) == 4


def test_average_of_empty_list_is_zero():
    assert average([]) == 0


def test_add_item_does_not_share_state_between_calls():
    assert add_item(1) == [1]
    assert add_item(2) == [2]


def test_find_user_returns_matching_row():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE users (name TEXT)")
    db.execute("INSERT INTO users VALUES ('ann')")
    assert find_user(db, "ann") == ("ann",)
'''

# Attempt 1 fixes the division by zero only: the mutable default and the secret are still there -> rejected.
FIX_ATTEMPT_1 = '''```python
import json

API_KEY = "demo-not-a-real-key-4f9a2c71"


def average(numbers):
    """Mean of numbers; 0.0 for an empty sequence."""
    numbers = list(numbers)
    return sum(numbers) / len(numbers) if numbers else 0.0


def find_user(db, name):
    cur = db.execute("SELECT * FROM users WHERE name = ?", (name,))
    return cur.fetchone()


def add_item(item, items=[]):
    items.append(item)
    return items
```
Fixed the empty-list case.'''

FIX_ATTEMPT_2 = '''```python
import os

API_KEY = os.environ.get("API_KEY", "")


def average(numbers):
    """Mean of numbers; 0.0 for an empty sequence."""
    numbers = list(numbers)
    return sum(numbers) / len(numbers) if numbers else 0.0


def find_user(db, name):
    """First user row with this name, or None. Parameterised query: no SQL injection."""
    cur = db.execute("SELECT * FROM users WHERE name = ?", (name,))
    return cur.fetchone()


def add_item(item, items=None):
    """Append to `items`, creating a fresh list per call (no shared mutable default)."""
    if items is None:
        items = []
    items.append(item)
    return items
```
- API_KEY now comes from the environment; the unused json import is gone.
- average() returns 0.0 for an empty input; find_user uses a bound parameter; add_item no longer shares a list.'''

REVIEW_JSON = """{"verdict": "request_changes",
 "summary": "The module has a hard-coded credential, an injectable SQL query and two correctness bugs. The executed tests confirm both bugs.",
 "strengths": ["Small, readable functions", "Each function does one thing"],
 "findings": [
  {"category": "security", "severity": "high", "line": 3, "title": "Hard-coded API key", "detail": "Credentials in source leak through version control.", "suggestion": "Read it from the environment."},
  {"category": "security", "severity": "high", "line": 14, "title": "SQL built with % formatting", "detail": "A crafted name can change the query.", "suggestion": "Use a bound parameter: execute(sql, (name,))."},
  {"category": "correctness", "severity": "high", "line": 10, "title": "average([]) raises ZeroDivisionError", "detail": "Confirmed by test_average_of_empty_list_is_zero.", "suggestion": "Return 0.0 for empty input."},
  {"category": "correctness", "severity": "medium", "line": 18, "title": "Mutable default argument", "detail": "items=[] is shared between calls (confirmed by a failing test).", "suggestion": "Use items=None and create the list inside."},
  {"category": "style", "severity": "low", "title": "Missing docstrings and an unused import", "suggestion": "Add docstrings; drop `import json`."}],
 "test_assessment": "2 of 4 generated tests fail, and both failures expose real bugs rather than doubtful tests.",
 "next_steps": ["Fix the two correctness bugs", "Move the key to the environment", "Parameterise the query"]}"""


def sample_agent(*, review_json: str = REVIEW_JSON, tests: str = TESTS, fixes=(FIX_ATTEMPT_1, FIX_ATTEMPT_2)):
    """The whole graph with scripted models, returned with the two fakes so tests can inspect their requests.

    The `fast` model serves root routing, the two tool-calling analysis stages and the fix summary; the `coder`
    model serves test_runner, the synthesizer and the fixer. Each FakeLlm is shared by several agents, so its
    script is the interleaved sequence of that model's calls in execution order.
    """
    fast = FakeLlm(script=[
        call("transfer_to_agent", agent_name="review_pipeline"),                           # root, turn 1
        call("analyze_code_structure"),
        "Notes: a data-access helper module. Hard-coded API_KEY (line 3), SQL built with % (line 14), mutable default "
        "(line 18). average() has no empty-input guard.",
        call("check_code_style"),
        "Notes: needs polish: missing docstrings and an unused import; layout is otherwise fine.",
        call("transfer_to_agent", agent_name="fix_pipeline"),                               # root, turn 2
        "Fixed the divide-by-zero, the shared default list, the SQL injection and the hard-coded key; validated by the "
        "review's own tests passing in the sandbox.",
    ])
    coder = FakeLlm(script=[
        call("run_generated_tests", test_code=tests),
        "Tested the mean, the empty input, state isolation and the SQL helper. 2 failed: empty-list average (real bug) "
        "and shared default list (real bug).",
        review_json,
        *fixes,
    ])
    return build_root_agent(Models(fast=fast, coder=coder)), fast, coder


RISKY_CODE = '''import subprocess


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True).stdout


def calc(expr):
    return eval(expr)
'''

LENIENT_REVIEW = ('{"verdict": "approve", "summary": "Tiny helpers, looks fine to me.", "strengths": ["Short"], '
                  '"findings": [], "test_assessment": "Looks tested.", "next_steps": []}')


def risky_agent():
    """A lazy reviewer model approves dangerous code; the deterministic policy must overrule it."""
    fast = FakeLlm(script=[
        call("transfer_to_agent", agent_name="review_pipeline"),
        call("analyze_code_structure"),
        "Notes: shell execution via subprocess(shell=True) and eval() of a caller-supplied string.",
        call("check_code_style"),
        "Notes: mostly clean; missing docstrings.",
    ])
    coder = FakeLlm(script=[
        call("run_generated_tests", test_code="from solution import calc\n\n\ndef test_calc():\n    assert calc('1+1') == 2\n"),
        "The sandbox refused to execute this module, so nothing was verified by running it.",
        LENIENT_REVIEW,
    ])
    return build_root_agent(Models(fast=fast, coder=coder)), fast, coder
