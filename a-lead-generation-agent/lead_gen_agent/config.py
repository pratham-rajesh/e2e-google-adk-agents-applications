"""Runtime configuration. Everything is overridable with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

try:  # optional, only for local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

FAST_MODEL = os.getenv("LEADGEN_FAST_MODEL", "gemini-2.5-flash")
SMART_MODEL = os.getenv("LEADGEN_SMART_MODEL", "gemini-2.5-pro")


def max_iterations() -> int:
    return int(os.getenv("LEADGEN_MAX_ITERATIONS", "3"))


def deep_dive_top_n() -> int:
    return int(os.getenv("LEADGEN_DEEP_DIVE_TOP_N", "6"))


def output_dir() -> Path:
    path = Path(os.getenv("LEADGEN_OUTPUT_DIR", "output"))
    path.mkdir(parents=True, exist_ok=True)
    return path


# State keys shared between agents. Centralised so typos fail loudly in tests.
class K:
    BRIEF = "research_brief"          # dict, saved by the coordinator tool
    BRIEF_TEXT = "brief_text"         # human-readable rendering for prompts
    APPROVED = "brief_approved"       # bool, set once the user says "go"
    RAW_FINDINGS = "raw_findings"     # free text from the google_search researcher
    BATCH = "lead_batch"              # structured extraction of the latest round
    LEADS = "leads"                   # list[dict] - deduplicated pool
    LEADS_SUMMARY = "leads_summary"   # prompt-friendly summary of the pool
    GAPS = "research_gaps"            # evaluator feedback for the next round
    RANKED = "ranked_leads"           # list[dict] sorted by score
    RANKED_MD = "ranked_leads_md"     # markdown table of the ranking
    DETAIL = "ranked_leads_detail"    # per-lead evidence text for the report
    LEAD_COUNT = "lead_count"
    TARGET_COUNT = "target_count"
    ROUND_TARGET = "round_target"     # how many new leads each discovery round should aim for
    CSV_PATH = "csv_path"
    REPORT = "final_report"
