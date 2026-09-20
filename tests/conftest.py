"""Model factories for the test suite.

The pydantic models in `buyerlist.schemas` are structured-output schemas, so
every field is required — a model is only useful to the API if it forces the
model to answer every question. That makes them verbose to build by hand, so
each factory below fills in a neutral default and takes `**overrides` for the
one or two fields a given test actually cares about.

Keeping the factories on the real models rather than on `SimpleNamespace` is
deliberate: a test that constructs a `FundProfile` fails the moment the schema
grows a required field, which is exactly when the prompt and the extraction
need revisiting too.
"""

from __future__ import annotations

import pytest

from buyerlist.schemas import (
    AddOn,
    CompanyProfile,
    Evidence,
    FundProfile,
    NaicsCode,
    Platform,
    SizeEstimate,
)


def make_addon(name: str = "Tuck-In Co", **kw) -> AddOn:
    return AddOn(**{"name": name, "announced": None, "city": None, "state": None, **kw})


def make_platform(name: str = "Apex Service Partners", **kw) -> Platform:
    return Platform(
        **{
            "name": name,
            "sector": "HVAC",
            "naics_codes": ["238220"],
            "description": "Residential HVAC roll-up.",
            "hq_state": "FL",
            "acquired_year": 2019,
            "role": "platform",
            "parent_platform": None,
            "add_ons": [],
            **kw,
        }
    )


def make_fund(firm_name: str = "Test Capital Partners", **kw) -> FundProfile:
    return FundProfile(
        **{
            "firm_name": firm_name,
            "website": "https://test.example",
            "hq_city": "Dallas",
            "hq_state": "TX",
            "other_offices": [],
            "control": "control",
            "deal_types": ["platform"],
            "ebitda_min_usd": None,
            "ebitda_max_usd": None,
            "ev_min_usd": None,
            "ev_max_usd": None,
            "revenue_min_usd": None,
            "revenue_max_usd": None,
            "naics_coverage": ["238"],
            "sector_themes": ["business services"],
            "geographies": ["North America"],
            "exclusions": [],
            "latest_fund": None,
            "latest_fund_size_usd": None,
            "platforms": [],
            "evidence": [],
            "extraction_confidence": "high",
            **kw,
        }
    )


def make_row(profile: FundProfile | None = None, **kw) -> dict:
    """An index row as `store.load_funds` returns it."""
    profile = profile or make_fund()
    return {
        "domain": kw.pop("domain", "test.example"),
        "firm_name": profile.firm_name,
        "website": profile.website,
        "tier": kw.pop("tier", "deep"),
        "discovered_via": None,
        "scraped_at": "2026-01-01",
        "pages_fetched": 9,
        "extract_conf": "high",
        "profile": profile,
        **kw,
    }


def make_company(legal_name: str = "Hoffmann Brothers", **kw) -> CompanyProfile:
    return CompanyProfile(
        **{
            "legal_name": legal_name,
            "dba": None,
            "hq_city": "St. Louis",
            "hq_state": "MO",
            "service_area": ["Greater St. Louis"],
            "naics_codes": [
                NaicsCode(code="238220", label="Plumbing, Heating and A/C", confidence="high")
            ],
            "description": "Residential and light-commercial mechanical contractor.",
            "products_services": ["HVAC install", "plumbing"],
            "end_markets": ["residential"],
            "business_model": ["field-service"],
            "year_founded": 1950,
            "locations_count": 2,
            "ownership_signals": ["family-owned"],
            "risk_flags": [],
            "size_signals": [],
            "evidence": [
                Evidence(
                    field="hq_city",
                    quote="serving St. Louis since 1950",
                    source_url="https://example.com/about",
                )
            ],
            "extraction_confidence": "high",
            "notes": "",
            **kw,
        }
    )


def make_size(**kw) -> SizeEstimate:
    return SizeEstimate(
        **{
            "revenue_low_usd": 40_000_000,
            "revenue_high_usd": 60_000_000,
            "ebitda_low_usd": 4_000_000,
            "ebitda_high_usd": 8_000_000,
            "margin_assumption_pct": 12.0,
            "signals_used": ["300+ employees"],
            "comparable_basis": "field service at ~$180k revenue per tech",
            "confidence": "medium",
            "caveats": ["service vs project mix"],
            **kw,
        }
    )


@pytest.fixture
def fund():
    return make_fund


@pytest.fixture
def row():
    return make_row
