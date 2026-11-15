"""Run the offline eval set (tests/eval/cases/*.json) and print a pass/fail table."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from code_review_assistant.evals import load_cases, run_case  # noqa: E402


def main() -> int:
    cases = load_cases()
    failed = 0
    for case in cases:
        problems = run_case(case)
        failed += bool(problems)
        print(f"{'PASS' if not problems else 'FAIL':5} {case['name']}")
        for p in problems:
            print(f"        - {p}")
    print(f"\n{len(cases) - failed}/{len(cases)} cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
