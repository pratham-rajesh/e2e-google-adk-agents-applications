"""Deterministic text similarity for ticket search and duplicate detection (no LLM, no embeddings).

Why not embeddings? A duplicate check has to be cheap, explainable and reproducible: the agent (and a
reviewer reading the trace) can see *which terms* made two tickets look alike. Token-overlap scoring is
good enough for bug reports, which are full of distinctive identifiers (exception names, endpoints).
"""

from __future__ import annotations

import math
import re
from collections import Counter

_STOP = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how i if in into is it its
    me my no not of on or our so than that the their then there these they this to was we were what when where
    which while who why will with would you your after before also just still some any all get got""".split()
)
_TOKEN = re.compile(r"[a-z0-9_]+")


def _stem(tok: str) -> str:
    """Very light suffix stripping so 'timeouts'/'timing out'/'timed' collapse a bit. Deliberately crude."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(tok) > len(suffix) + 3 and tok.endswith(suffix):
            return tok[: -len(suffix)]
    return tok


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def term_counts(title: str, body: str = "") -> Counter:
    """Title terms count double: a ticket's title is the densest description of the problem."""
    c: Counter = Counter()
    for t in tokenize(title):
        c[t] += 2
    for t in tokenize(body):
        c[t] += 1
    return c


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[t] * b[t] for t in a.keys() & b.keys())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def coverage(query_terms: Counter, doc_terms: Counter) -> float:
    """Fraction of the query's distinct terms that appear in the document (recall-oriented, for search)."""
    if not query_terms:
        return 0.0
    return sum(1 for t in query_terms if t in doc_terms) / len(query_terms)


def similarity(title_a: str, body_a: str, title_b: str, body_b: str) -> float:
    return cosine(term_counts(title_a, body_a), term_counts(title_b, body_b))


def shared_terms(a: Counter, b: Counter, limit: int = 8) -> list[str]:
    """The terms that explain a match, strongest first (surfaced to the agent as the 'why')."""
    common = a.keys() & b.keys()
    return [t for t, _ in sorted(((t, a[t] * b[t]) for t in common), key=lambda x: (-x[1], x[0]))][:limit]
