"""Pydantic models shared by tools and agents."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchBrief(BaseModel):
    """What the user wants, normalised into an Ideal Customer Profile + search plan."""

    product_description: str = Field(description="What the user sells, in one or two sentences.")
    target_industries: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list, description="Countries / regions to focus on.")
    company_size: str = Field(default="", description="e.g. '50-500 employees' or 'Series A-B'.")
    buyer_personas: list[str] = Field(default_factory=list, description="Job titles of the buyer.")
    buy_signals: list[str] = Field(
        default_factory=list,
        description="Observable events that suggest a company is ready to buy "
        "(hiring, funding, product launch, new regulation...).",
    )
    target_lead_count: int = Field(default=10, ge=1, le=50)
    search_angles: list[str] = Field(
        default_factory=list, description="Distinct research angles / queries for discovery."
    )


class Lead(BaseModel):
    company_name: str
    website: str = Field(default="", description="Official homepage URL if known.")
    hq_location: str = ""
    industry: str = ""
    size_hint: str = Field(default="", description="Employee count, funding stage, etc.")
    description: str = Field(default="", description="One sentence on what the company does.")
    why_fit: str = Field(default="", description="Why this company matches the ICP.")
    source_urls: list[str] = Field(default_factory=list)


class LeadBatch(BaseModel):
    """Structured extraction of one discovery round."""

    leads: list[Lead] = Field(default_factory=list)
