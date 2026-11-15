"""Structured outputs. `ReviewReport` is the contract of the feedback synthesizer.

Gemini can be given it as a hard `output_schema`. Other providers get the same shape described in the prompt
and `parse_report` validates leniently (code fences, prose around the JSON, wrong casing), so a small local
model that almost follows the format still produces a usable report.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

Severity = Literal["critical", "high", "medium", "low", "info"]
Verdict = Literal["approve", "approve_with_comments", "request_changes"]
SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class Finding(BaseModel):
    category: str = Field(description="security | correctness | maintainability | style | testing | performance")
    severity: Severity
    title: str = Field(description="One line")
    line: Optional[int] = Field(default=None, description="1-based line in the submitted code, if known")
    detail: str = Field(default="", description="Why it matters")
    suggestion: str = Field(default="", description="Concrete fix")

    @field_validator("severity", mode="before")
    @classmethod
    def _norm_severity(cls, v: Any) -> Any:
        v = str(v).strip().lower()
        return {"warning": "medium", "error": "high", "minor": "low", "major": "high", "blocker": "critical"}.get(v, v)


class ReviewReport(BaseModel):
    verdict: Verdict
    summary: str = Field(description="Two or three sentences for the author")
    strengths: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    test_assessment: str = Field(default="", description="What the executed tests show and how far to trust them")
    next_steps: list[str] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _norm_verdict(cls, v: Any) -> Any:
        v = re.sub(r"[\s-]+", "_", str(v).strip().lower())
        return {"approved": "approve", "reject": "request_changes", "changes_requested": "request_changes",
                "needs_changes": "request_changes", "approve_with_comment": "approve_with_comments"}.get(v, v)


def _first_json_object(text: str) -> str | None:
    """The first balanced {...} in `text`, ignoring braces inside strings."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def parse_report(raw: Any) -> ReviewReport | None:
    """Validate `raw` (dict, JSON text, or text containing a JSON object) into a ReviewReport, or None."""
    if isinstance(raw, ReviewReport):
        return raw
    data: Any = raw
    if isinstance(raw, str):
        body = _first_json_object(raw)
        if body is None:
            return None
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    try:
        return ReviewReport.model_validate(data)
    except ValidationError:
        return None
