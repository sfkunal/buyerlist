"""Turn a fetched site into a CompanyProfile, and estimate size from proxies.

Two separate calls on purpose. Extraction is near-mechanical reading; size
estimation is inference from indirect evidence and benefits from a stronger
model and real reasoning. Keeping them apart also means a bad size estimate
never corrupts the underlying facts.
"""

from __future__ import annotations

from .fetch import SiteSnapshot
from .llm import LLM, MODEL_COMPANY_EXTRACT, MODEL_REASONING
from .schemas import CompanyProfile, SizeEstimate

EXTRACT_SYSTEM = """\
You extract structured facts about a small or medium-sized business from its own website, \
for an M&A analyst assembling a buyer list.

Rules:
- Only state what the pages support. If a field is not evidenced, use null or an empty list.
- Never infer revenue, headcount, or ownership from vibes. If the site does not say, leave it out \
and record what you DID see in size_signals instead.
- Quote verbatim in evidence. Do not paraphrase inside a quote.
- Evidence coverage is not optional. Every non-null scalar field (legal_name, hq_city, hq_state, \
year_founded, locations_count) and every non-empty list field needs at least one evidence entry \
whose quote actually contains the fact. A quote that merely sits near the fact does not count: \
if you claim hq_city is St. Louis, the quote must contain "St. Louis". If no quote on the page \
contains the fact, set the field to null rather than inferring it.
- naics_codes is the join key to PE fund sector coverage, so be precise. Prefer the most specific \
code the evidence supports, and include a broader parent code as a second entry.
- size_signals should be generous: headcount visible on a team page, number of locations, fleet or \
truck counts, "serving X customers since Y", listed equipment, certifications, open roles. These \
are the only inputs a downstream estimator will get, so capture anything quantitative.
- risk_flags are things a buyer would diligence, stated neutrally. If the site is built entirely \
around one named founder, that is owner dependence. If it names a single large customer, that is \
concentration. Empty list is a valid answer.
"""

SIZE_SYSTEM = """\
You estimate revenue and EBITDA for a private SMB from indirect evidence, for an M&A analyst.

You will not be given financials, because the company does not publish them. Reason from the \
proxies you are given and from sector benchmarks you know.

Rules:
- Output a RANGE, not a point estimate. The range should be wide enough that you would be \
surprised to be outside it.
- State the benchmark you applied in comparable_basis, with its unit economics, e.g. \
"residential HVAC field service, roughly $180-250k revenue per service technician".
- margin_assumption_pct should reflect the sector, not a generic number. Field services, \
distribution, and niche manufacturing differ substantially.
- If the proxies are thin, say so: widen the range and set confidence to low. An honest wide \
range is far more useful than a confident wrong number.
- caveats should name what would most change the estimate if you learned it.
"""


def extract_company(llm: LLM, snapshot: SiteSnapshot) -> CompanyProfile:
    if snapshot.degraded and not snapshot.pages:
        raise RuntimeError(
            f"Cannot extract from {snapshot.root_url}: " + "; ".join(snapshot.notes)
        )

    content = (
        f"Website: {snapshot.root_url}\n"
        f"Pages fetched: {len(snapshot.pages)}\n\n"
        f"{snapshot.to_prompt_text()}"
    )
    return llm.parse(
        stage="extract",
        model=MODEL_COMPANY_EXTRACT,
        schema=CompanyProfile,
        system=EXTRACT_SYSTEM,
        user_content=content,
        max_tokens=8000,
        effort="medium",
    )


def estimate_size(llm: LLM, profile: CompanyProfile) -> SizeEstimate:
    signals = "\n".join(
        f"- [{s.kind}] {s.signal}  ({s.source_url})" for s in profile.size_signals
    ) or "- (none captured)"

    naics = ", ".join(f"{n.code} {n.label}" for n in profile.naics_codes) or "unknown"

    content = f"""\
Company: {profile.legal_name}{f' (dba {profile.dba})' if profile.dba else ''}
Sector (NAICS): {naics}
Description: {profile.description}
Business model: {', '.join(profile.business_model) or 'unclear'}
End markets: {', '.join(profile.end_markets) or 'unclear'}
Locations: {profile.locations_count if profile.locations_count is not None else 'unknown'}
Service area: {', '.join(profile.service_area) or 'unknown'}
Founded: {profile.year_founded or 'unknown'}
Ownership signals: {', '.join(profile.ownership_signals) or 'none'}

Size proxies observed on the site:
{signals}

Extraction confidence was: {profile.extraction_confidence}
Notes from extraction: {profile.notes}
"""
    return llm.parse(
        stage="size",
        model=MODEL_REASONING,
        schema=SizeEstimate,
        # Thinking is on by default on opus-5 and shares this budget with the
        # visible answer, so 4000 at effort="high" truncated the estimate on any
        # target with a rich set of proxies — exactly the cases worth reasoning
        # hardest about.
        max_tokens=12000,
        system=SIZE_SYSTEM,
        user_content=content,
        effort="high",
    )
