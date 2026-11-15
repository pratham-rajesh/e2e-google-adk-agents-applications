"""Keyless web search for the research agent.

Gemini's built-in `google_search` grounding only works with Gemini models, so other providers get a
plain DuckDuckGo (`ddgs`) function tool. `search_tools()` picks the right one.
"""

from __future__ import annotations

import asyncio
import os

from google.adk.tools import google_search

from .. import models


def _ddg_search(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS

    rows = DDGS().text(query, max_results=max_results)
    return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in rows]


async def web_search(query: str, max_results: int = 6) -> dict:
    """Search the public web and return titles, URLs and snippets.

    Args:
        query: A specific query, e.g. an exact error message plus the framework name.
        max_results: Number of results to return (1-10).

    Returns:
        dict with `status` and a `results` list of {title, url, snippet}. Cite the `url`s you rely on.
    """
    max_results = max(1, min(int(max_results), 10))
    try:
        results = await asyncio.to_thread(_ddg_search, query, max_results)
    except Exception as exc:  # network / rate-limit problems go back to the model, not up the stack
        if "no results" in str(exc).lower():
            return {"status": "ok", "results": [], "hint": "No results; try a shorter, broader query."}
        return {"status": "error", "message": f"{type(exc).__name__}: {exc}", "results": []}
    return {"status": "ok", "results": results}


def search_tools() -> list:
    """`BUGASSIST_SEARCH_BACKEND`: auto (default) | google | ddg."""
    backend = os.getenv("BUGASSIST_SEARCH_BACKEND", "auto").lower()
    if backend == "google" or (backend == "auto" and models.is_gemini()):
        return [google_search]
    return [web_search]
