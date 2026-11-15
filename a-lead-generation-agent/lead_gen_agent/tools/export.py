"""CSV export of the ranked lead list."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

FIELDS = [
    "rank", "company_name", "score", "website", "hq_location", "industry", "size_hint",
    "description", "why_fit", "emails", "social", "signals", "sources",
]


def export_csv(ranked: list[dict], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"leads_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for i, lead in enumerate(ranked, 1):
            enrich = lead.get("enrichment") or {}
            writer.writerow(
                {
                    "rank": i,
                    "company_name": lead.get("company_name", ""),
                    "score": lead.get("score", ""),
                    "website": lead.get("website", ""),
                    "hq_location": lead.get("hq_location", ""),
                    "industry": lead.get("industry", ""),
                    "size_hint": lead.get("size_hint", ""),
                    "description": lead.get("description", ""),
                    "why_fit": lead.get("why_fit", ""),
                    "emails": "; ".join(enrich.get("emails", [])),
                    "social": "; ".join(enrich.get("social", [])),
                    "signals": (lead.get("signals") or "").replace("\n", " ")[:500],
                    "sources": "; ".join(lead.get("source_urls", [])),
                }
            )
    return path
