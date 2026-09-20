"""Shared data models.

These are used in two directions by the same extraction engine:
  - pointed at an SMB's website  -> CompanyProfile
  - pointed at a PE firm's site  -> FundProfile

Structured-output constraints worth remembering: no recursive schemas, no
numeric/length constraints (the SDK strips them and validates client-side), and
every object needs additionalProperties=false. Keeping these models flat and
shallow also keeps first-call schema compilation fast.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


# --------------------------------------------------------------------------
# Target company
# --------------------------------------------------------------------------


class Evidence(BaseModel):
    """One traceable claim. Every non-obvious field should have one of these."""

    field: str = Field(description="Dotted path of the field this supports, e.g. 'hq_state'.")
    quote: str = Field(description="Verbatim text from the page. Do not paraphrase.")
    source_url: str = Field(description="The page the quote came from.")


class NaicsCode(BaseModel):
    code: str = Field(description="NAICS code, 2-6 digits, as a string.")
    label: str = Field(description="Human-readable label for the code.")
    confidence: Confidence


class SizeSignal(BaseModel):
    """A raw proxy observation the size estimator will reason over."""

    signal: str = Field(description="What was observed, e.g. '18 named staff on team page'.")
    kind: Literal[
        "headcount",
        "locations",
        "fleet",
        "customers",
        "tenure",
        "equipment",
        "certifications",
        "hiring",
        "capacity",
        "other",
    ]
    source_url: str


class CompanyProfile(BaseModel):
    legal_name: str
    dba: str | None = Field(description="Trading name if different from legal name, else null.")

    hq_city: str | None
    hq_state: str | None
    service_area: list[str] = Field(
        description="States or metros served, as stated on the site. Empty if unclear."
    )

    naics_codes: list[NaicsCode] = Field(
        description="1-3 NAICS codes, most specific first. This is the join key to fund coverage."
    )
    description: str = Field(description="Two sentences, neutral, no marketing language.")
    products_services: list[str]
    end_markets: list[str] = Field(
        description="Who buys: residential, commercial, industrial, municipal, healthcare, etc."
    )
    business_model: list[str] = Field(
        description="Tags: recurring, project-based, contract, field-service, "
        "distribution, manufacturing, e-commerce, franchise."
    )

    year_founded: int | None
    locations_count: int | None

    ownership_signals: list[str] = Field(
        description="e.g. founder-led, family-owned, already PE-backed, ESOP, franchisee."
    )
    risk_flags: list[str] = Field(
        description="Things a buyer would diligence: owner dependence, customer "
        "concentration hints, cyclicality, licensing exposure. Empty if none evident."
    )

    size_signals: list[SizeSignal] = Field(
        description="Raw proxies for scale. Collect generously; the size estimator uses these."
    )

    evidence: list[Evidence]
    extraction_confidence: Confidence
    notes: str = Field(description="What could not be determined from the site, if anything.")


class SizeEstimate(BaseModel):
    revenue_low_usd: int
    revenue_high_usd: int
    ebitda_low_usd: int
    ebitda_high_usd: int
    margin_assumption_pct: float = Field(description="EBITDA margin assumed, as a percent.")
    signals_used: list[str] = Field(description="Which proxies drove the estimate.")
    comparable_basis: str = Field(
        description="The benchmark applied, e.g. 'residential HVAC ~$200k revenue per tech'."
    )
    confidence: Confidence
    caveats: list[str]


# --------------------------------------------------------------------------
# PE funds
# --------------------------------------------------------------------------


class AddOn(BaseModel):
    name: str
    announced: str | None = Field(description="YYYY-MM if known, else null.")
    city: str | None
    state: str | None


class Platform(BaseModel):
    """A portfolio company. Deep-tier funds carry add-on history here.

    This is the matching unit that makes output credible: deals happen because a
    fund owns a roll-up in an adjacent market, not because it 'likes services'.
    """

    name: str
    sector: str
    naics_codes: list[str] = Field(
        description="NAICS for what this company ACTUALLY DOES, not the firm's sector label."
    )
    description: str
    hq_state: str | None
    acquired_year: int | None
    role: Literal["platform", "add_on", "unknown"] = Field(
        description="'add_on' if the site labels this an add-on/tuck-in acquisition rather "
        "than a standalone platform investment. 'unknown' if not stated."
    )
    parent_platform: str | None = Field(
        description="If this is an add-on and the site names the platform it was acquired "
        "into, that platform's name. Otherwise null."
    )
    add_ons: list[AddOn]


class FundProfile(BaseModel):
    firm_name: str
    website: str

    hq_city: str | None
    hq_state: str | None
    other_offices: list[str]

    control: Literal["control", "minority", "both", "unknown"]
    deal_types: list[str] = Field(description="platform, add-on, recap, growth, carve-out.")

    ebitda_min_usd: int | None
    ebitda_max_usd: int | None
    ev_min_usd: int | None
    ev_max_usd: int | None
    revenue_min_usd: int | None
    revenue_max_usd: int | None

    naics_coverage: list[str] = Field(description="NAICS codes the firm's stated sectors map to.")
    sector_themes: list[str] = Field(description="Verbatim sector language from the site.")
    geographies: list[str] = Field(description="Stated footprint. 'North America' is acceptable.")
    exclusions: list[str] = Field(description="What they explicitly will not do.")

    latest_fund: str | None
    latest_fund_size_usd: int | None

    platforms: list[Platform]

    evidence: list[Evidence]
    extraction_confidence: Confidence


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


class DimensionScore(BaseModel):
    dimension: Literal[
        "sector_fit",
        "size_fit",
        "geographic_fit",
        "addon_thesis",
        "recent_activity",
        "deal_type_fit",
    ]
    score: int = Field(description="0-5 inclusive.")
    reason: str = Field(description="One clause. Cite the platform or criterion that earned it.")


class Match(BaseModel):
    firm_name: str = Field(
        description="MUST be copied verbatim from the provided candidate list. "
        "Never invent or recall a firm name."
    )
    via_platform: str | None = Field(
        description="Platform company this thesis runs through, verbatim from the "
        "candidate list, or null for a direct platform investment."
    )
    dimension_scores: list[DimensionScore]
    score: int = Field(description="Weighted total, 0-100.")
    tier: Literal["tier_1", "tier_2", "wildcard"]
    rationale: str = Field(description="Two to three sentences. Lead with the thesis.")
    angle: str = Field(description="One sentence: how you'd open the conversation.")
    evidence: list[str] = Field(description="Specific facts from the index that support this.")
    confidence: Confidence


class RankedMatches(BaseModel):
    matches: list[Match]
    excluded_note: str = Field(
        description="Brief note on notable candidates considered and dropped, and why."
    )


class Disqualifier(BaseModel):
    firm_name: str
    objection: str = Field(description="The strongest argument against this match.")
    severity: Literal["fatal", "material", "minor"]


class Critique(BaseModel):
    disqualifiers: list[Disqualifier]
    overall_note: str
