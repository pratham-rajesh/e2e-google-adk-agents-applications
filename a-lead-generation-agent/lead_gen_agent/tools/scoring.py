"""Deterministic lead pooling and ICP-fit scoring (no LLM involved, fully unit-tested)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_SUFFIXES = re.compile(r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|plc|pvt|sa|ag)\.?$", re.I)


def normalize_domain(website: str) -> str:
    if not website:
        return ""
    host = urlparse(website if "://" in website else "https://" + website).hostname or ""
    return host.lower().removeprefix("www.")


def normalize_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    prev = None
    while prev != name:  # strip stacked suffixes like "Acme Co Ltd"
        prev, name = name, _SUFFIXES.sub("", name).strip()
    return name


def lead_key(lead: dict) -> str:
    return normalize_domain(lead.get("website", "")) or normalize_name(lead.get("company_name", ""))


_MERGE_FIELDS = ("website", "hq_location", "industry", "size_hint", "description", "why_fit")


def merge_leads(pool: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """Merge `new` into `pool` by domain/name. Returns (merged_pool, number_of_genuinely_new)."""
    merged = [dict(l) for l in pool]
    index = {lead_key(l): l for l in merged if lead_key(l)}
    added = 0
    for lead in new:
        key = lead_key(lead)
        if not key:
            continue
        if key in index:
            existing = index[key]
            for field in _MERGE_FIELDS:
                if not existing.get(field) and lead.get(field):
                    existing[field] = lead[field]
            existing["source_urls"] = sorted(
                set(existing.get("source_urls", [])) | set(lead.get("source_urls", []))
            )
        else:
            entry = dict(lead)
            merged.append(entry)
            index[key] = entry
            added += 1
    return merged, added


def _matches(haystack: str, needles: list[str]) -> list[str]:
    hay = haystack.lower()
    return [n for n in needles if n and n.lower() in hay]


def score_lead(lead: dict, brief: dict) -> dict:
    """Score a lead 0-100 against the brief. Returns {'score': int, 'breakdown': {...}}.

    Weights: industry 30, region 20, buy signals 25, contactability 15, evidence 10.
    A criterion the user left empty ("any") earns full marks for industry/region and half
    marks for signals so that an unspecified preference neither helps nor hurts ranking.
    """
    text = (
        " ".join(str(lead.get(f, "")) for f in ("industry", "description", "why_fit", "size_hint"))
        + " "
        + str(lead.get("signals", ""))
    )
    breakdown: dict[str, int] = {}

    industries = brief.get("target_industries", [])
    breakdown["industry"] = 30 if (not industries or _matches(text, industries)) else 0

    regions = brief.get("regions", [])
    loc = lead.get("hq_location", "") + " " + lead.get("size_hint", "")
    breakdown["region"] = 20 if (not regions or _matches(loc, regions)) else 0

    signals = brief.get("buy_signals", [])
    if signals:
        hits = len(_matches(text, signals))
        breakdown["signals"] = round(25 * min(hits, len(signals)) / len(signals))
    else:
        breakdown["signals"] = 12

    enrich = lead.get("enrichment") or {}
    breakdown["contact"] = (10 if enrich.get("emails") else 0) + (5 if enrich.get("social") else 0)
    breakdown["evidence"] = min(len(lead.get("source_urls", [])), 2) * 5

    return {"score": sum(breakdown.values()), "breakdown": breakdown}


def rank_leads(leads: list[dict], brief: dict) -> list[dict]:
    ranked = []
    for lead in leads:
        scored = score_lead(lead, brief)
        ranked.append({**lead, "score": scored["score"], "score_breakdown": scored["breakdown"]})
    ranked.sort(key=lambda l: (-l["score"], l.get("company_name", "").lower()))
    return ranked


def ranking_markdown(ranked: list[dict]) -> str:
    rows = [
        "| # | Company | Score | Location | Website | Contact | Evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, l in enumerate(ranked, 1):
        emails = ", ".join((l.get("enrichment") or {}).get("emails", [])) or "-"
        srcs = ", ".join(l.get("source_urls", [])[:2]) or "-"
        rows.append(
            f"| {i} | {l.get('company_name', '')} | {l['score']} | {l.get('hq_location', '') or '-'} "
            f"| {l.get('website', '') or '-'} | {emails} | {srcs} |"
        )
    return "\n".join(rows)
