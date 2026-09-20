"""Filter the fund index to plausible candidates, then rank them exhaustively.

Two stages, deliberately split:

  Stage A is pure Python. No model sees a fund it has no business considering.
  Stage B is one cached model call that scores every survivor against a fixed
  rubric. At this index size, exhaustive scoring beats approximate retrieval —
  and it removes any need for an embedding dependency.

The filter is permissive by design: it excludes only on positive evidence of
mismatch. Roughly a third of firms publish an EBITDA band at all, so treating a
missing band as a failed test would silently delete most of the universe.
"""

from __future__ import annotations

import re
import sqlite3

from .llm import LLM, MODEL_REASONING
from .schemas import Critique, RankedMatches, SizeEstimate, CompanyProfile
from .index.store import compact_index, load_funds

# Language that means "we go anywhere in North America" rather than a real
# geographic constraint.
NATIONAL_TERMS = {
    "north america", "national", "nationwide", "united states", "us", "u.s.",
    "usa", "u.s.a.", "continental us", "lower 48", "global", "worldwide",
    "north american",
}

# The index writes geography both ways — "Texas" in one firm's criteria page,
# "TX" in another's — so a filter that understands only one form is half a
# filter. DC is included because firms list it alongside states.
STATE_NAMES: dict[str, str] = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "dc": "district of columbia", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana",
    "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan",
    "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio",
    "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
    "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming",
}
NAME_TO_CODE: dict[str, str] = {name: code for code, name in STATE_NAMES.items()}

# Multi-state regions. Expanding these to member states would invent a claim the
# fund never made (is Missouri "Midwest" or "Central"? firms disagree), so they
# are treated as an unreadable constraint rather than a mismatch — see _geo_ok.
REGION_TERMS = {
    "midwest", "midwestern", "northeast", "northeastern", "southeast",
    "southeastern", "southwest", "southwestern", "northwest", "northwestern",
    "west coast", "east coast", "gulf coast", "mid atlantic", "mid-atlantic",
    "atlantic", "pacific northwest", "pacific", "great lakes", "mountain west",
    "rocky mountain", "rockies", "sun belt", "sunbelt", "new england",
    "south", "southern", "north", "northern", "west", "western", "east",
    "eastern", "central", "midcontinent", "plains", "eastern seaboard",
}

_WORDS_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> str:
    """Lowercase word stream, space-delimited on both ends.

    Padding with spaces turns a plain `in` test into a word-boundary test, which
    also handles the multi-word cases ("new york", "u.s.a." -> "u s a") that a
    naive token set would miss.
    """
    return " " + " ".join(_WORDS_RE.findall(text.lower())) + " "


_NATIONAL_WORDS = tuple(_words(t).strip() for t in NATIONAL_TERMS)
_REGION_WORDS = tuple(_words(t).strip() for t in REGION_TERMS)


def _bands_overlap(
    lo_a: int | None, hi_a: int | None, lo_b: int | None, hi_b: int | None
) -> bool:
    """True unless the two ranges provably do not intersect.

    Either side being unknown means we cannot rule the fund out, so we keep it.
    """
    if lo_b is None and hi_b is None:
        return True
    lo_a = lo_a or 0
    hi_a = hi_a or 10**12
    lo_b = lo_b or 0
    hi_b = hi_b or 10**12
    return lo_a <= hi_b and lo_b <= hi_a


def _state_code(raw: str) -> str | None:
    """Resolve "MO" or "Missouri" to a canonical two-letter code."""
    s = " ".join(_WORDS_RE.findall(raw.lower()))
    if s in STATE_NAMES:
        return s
    return NAME_TO_CODE.get(s)


_CODE_RE = re.compile(r"\b[A-Z]{2}\b")


def _states_named(raw: str, blob_words: str) -> set[str]:
    """Every state the geography blob positively names, by code or by name.

    Full names are matched case-insensitively, but two-letter codes are matched
    only in their uppercase form against the original text. Word boundaries
    alone are not enough for codes: half of them are also ordinary English words
    ("in", "or", "me", "ok", "la"), so "primarily in Texas" would otherwise read
    as a claim about Indiana.
    """
    named = {c.lower() for c in _CODE_RE.findall(raw) if c.lower() in STATE_NAMES}
    named |= {
        code for code, name in STATE_NAMES.items() if f" {name} " in blob_words
    }
    return named


def _geo_ok(target_state: str | None, geographies: list[str]) -> bool:
    """True unless the fund's stated geography provably excludes the target.

    Matching is on word boundaries and understands both forms of a state, so a
    fund listing "Missouri" keeps a target in MO, and — unlike a substring test
    — a target in IN is no longer kept by the word "industrials".

    Regions ("Midwest", "Southeast") are deliberately not expanded to member
    states — firms disagree about the membership, so expanding would invent a
    claim. A blob whose only geographic content is regional is therefore treated
    as an unreadable constraint and passes, rather than being dropped for all 50
    states. Anything else that names no matching state is still a reject, which
    is the existing behaviour. The cost is that region-only funds are never
    geo-filtered; the ranker still scores geographic_fit on them.
    """
    if not geographies or not target_state:
        return True
    raw = " ".join(geographies)
    blob = _words(raw)
    if any(f" {term} " in blob for term in _NATIONAL_WORDS):
        return True

    code = _state_code(target_state)
    if code is None:
        # An unparseable target state is not evidence of a mismatch.
        return True

    named = _states_named(raw, blob)
    if named:
        return code in named
    # No state named at all: pass only if the blob is regional (unreadable, not
    # contradictory). Otherwise this is the existing no-match reject.
    return any(f" {term} " in blob for term in _REGION_WORDS)


def _naics_ok(target_codes: list[str], coverage: list[str]) -> bool:
    """Overlap at the 2-digit sector level. Unknown coverage is not a rejection."""
    if not coverage or not target_codes:
        return True
    t2 = {c[:2] for c in target_codes if c}
    c2 = {c[:2] for c in coverage if c}
    return bool(t2 & c2)


def _fund_naics_universe(p) -> list[str]:
    """Every NAICS code a fund touches, including through its portfolio.

    Filtering on a firm's stated sector language alone loses the best matches.
    Alpine describes itself as software and services (NAICS 51/54/56), so an
    HVAC target (23) fails a fund-level test — even though Alpine owns Apex
    Service Partners, an HVAC roll-up, which is precisely the right buyer.
    What a fund has actually bought is stronger evidence than how it markets
    itself, so portfolio codes count too.
    """
    codes = list(p.naics_coverage)
    for plat in p.platforms:
        codes.extend(plat.naics_codes)
    return codes


def hard_filter(
    conn: sqlite3.Connection,
    profile: CompanyProfile,
    size: SizeEstimate,
    widen_low_confidence: bool = True,
) -> tuple[list[dict], dict]:
    """Return (candidates, diagnostics). Pure Python, no model involved."""
    rows = load_funds(conn)
    target_codes = [n.code for n in profile.naics_codes]

    lo, hi = size.ebitda_low_usd, size.ebitda_high_usd
    if widen_low_confidence and size.confidence == "low":
        # A low-confidence estimate should widen the net, not narrow it.
        lo, hi = int(lo * 0.5), int(hi * 2.0)

    kept: list[dict] = []
    drops = {"size": 0, "naics": 0, "geo": 0, "exclusion": 0}

    for row in rows:
        p = row["profile"]

        if not _bands_overlap(lo, hi, p.ebitda_min_usd, p.ebitda_max_usd):
            drops["size"] += 1
            continue
        if not _naics_ok(target_codes, _fund_naics_universe(p)):
            drops["naics"] += 1
            continue
        if not _geo_ok(profile.hq_state, p.geographies):
            drops["geo"] += 1
            continue

        excl = " ".join(p.exclusions).lower()
        if excl and any(
            term in excl
            for term in [c.label.lower() for c in profile.naics_codes] + profile.end_markets
        ):
            drops["exclusion"] += 1
            continue

        kept.append(row)

    return kept, {"universe": len(rows), "kept": len(kept), "dropped": drops}


RANK_RUBRIC = """\
You rank private equity firms as plausible acquirers of a specific small business, for an M&A \
analyst building a buyer list.

THE ONE INVIOLABLE RULE
You may only name firms and platform companies that appear in the CANDIDATES list in the user \
message. Never introduce a firm from your own knowledge, never correct a firm's name, and never \
invent a platform. If you believe an obvious buyer is missing, say so in excluded_note rather \
than adding it. A fabricated name makes the entire output worthless.

SCORING — score each dimension 0-5 and weight as shown:
- sector_fit (25%): does the firm actually buy in this NAICS? A portfolio company in the same
  sector is worth more than a sector theme on their website.
- size_fit (20%): does the target's EBITDA sit inside the firm's stated band? Score against the
  MIDPOINT of the target's estimated range, not its edges. A firm with no published band is not
  penalised; score 2-3 and say the band is unknown.
- geographic_fit (15%): does the firm or one of its platforms operate in or adjacent to the
  target's market?
- addon_thesis (25%): the highest-value signal. Does the firm own a PLATFORM that is rolling up
  this exact kind of business, in an adjacent geography? A named platform with a pattern of
  similar add-ons is a 5. A generic sector match with no platform is a 1.
- recent_activity (10%): evidence of deals in this sector recently.
- deal_type_fit (5%): does control/minority and platform/add-on match what this seller likely
  wants?

TIERS
- tier_1: a specific, defensible thesis, usually via a named platform. Expect few of these.
- tier_2: plausible on sector and size, but no specific angle.
- wildcard: a real but non-obvious argument worth one line in a memo.

WRITING
- rationale: 2-3 sentences, thesis first. Name the platform and what it has been buying.
- angle: one sentence an analyst could actually open an email with.
- evidence: specific facts drawn from the candidate list only.
Return at most 12 matches, best first.
"""

CRITIQUE_SYSTEM = """\
You are the skeptic on an M&A deal team. For each proposed buyer, make the strongest honest \
argument AGAINST the match.

Look for: the target being below or above the firm's real size range; geographic stretch being \
described as adjacency; sector adjacency that does not survive contact with how the business \
actually makes money; a firm late in its fund life; a platform that has stopped acquiring; \
deal-type mismatch (a minority investor for a seller who wants out).

Severity: "fatal" if the match should be dropped, "material" if it needs a caveat in the memo, \
"minor" otherwise. Be concise and specific. If a match is genuinely sound, do not invent an \
objection — omit it.
"""


def _target_block(profile: CompanyProfile, size: SizeEstimate) -> str:
    naics = ", ".join(f"{n.code} ({n.label})" for n in profile.naics_codes)
    return f"""\
TARGET COMPANY
Name: {profile.legal_name}
Location: {profile.hq_city or '?'}, {profile.hq_state or '?'}
Service area: {', '.join(profile.service_area) or 'unknown'}
NAICS: {naics}
Description: {profile.description}
Products/services: {', '.join(profile.products_services[:10])}
End markets: {', '.join(profile.end_markets)}
Business model: {', '.join(profile.business_model)}
Founded: {profile.year_founded or 'unknown'} | Locations: {profile.locations_count or 'unknown'}
Ownership signals: {', '.join(profile.ownership_signals) or 'none noted'}
Risk flags: {'; '.join(profile.risk_flags) or 'none noted'}

SIZE ESTIMATE (derived from website proxies, not financials)
Revenue: ${size.revenue_low_usd:,} - ${size.revenue_high_usd:,}
EBITDA:  ${size.ebitda_low_usd:,} - ${size.ebitda_high_usd:,} (assumed {size.margin_assumption_pct}% margin)
Midpoint EBITDA: ${(size.ebitda_low_usd + size.ebitda_high_usd)//2:,}
Confidence: {size.confidence}
Basis: {size.comparable_basis}
"""


def rank(
    llm: LLM,
    conn: sqlite3.Connection,
    profile: CompanyProfile,
    size: SizeEstimate,
    candidates: list[dict],
) -> RankedMatches:
    """Score every candidate in one call, with the index as a cached prefix.

    The full index goes in the system prompt behind a cache breakpoint because it
    is byte-identical on every run; only the target and the candidate shortlist
    vary. After the first call the index bills at roughly a tenth of input rate.
    """
    full_index = compact_index(conn)
    candidate_names = [r["profile"].firm_name for r in candidates]

    system = [
        {"type": "text", "text": RANK_RUBRIC},
        {
            "type": "text",
            "text": "FUND INDEX (reference; the authoritative record of every firm):\n\n"
            + full_index,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]

    user = (
        _target_block(profile, size)
        + "\n\nCANDIDATES — score only these firms, named exactly as written here:\n"
        + "\n".join(f"- {n}" for n in candidate_names)
    )

    return llm.parse(
        stage="rank",
        model=MODEL_REASONING,
        schema=RankedMatches,
        system=system,
        user_content=user,
        max_tokens=16000,
        effort="high",
        cache_ttl="1h",  # must match the cache_control above so cost is priced right
    )


def critique(
    llm: LLM, profile: CompanyProfile, size: SizeEstimate, ranked: RankedMatches, top_n: int = 12
) -> Critique:
    top = ranked.matches[:top_n]
    blocks = "\n\n".join(
        f"{m.firm_name}"
        + (f" (via {m.via_platform})" if m.via_platform else "")
        + f"\n  score {m.score} / {m.tier}\n  {m.rationale}\n  evidence: {'; '.join(m.evidence[:4])}"
        for m in top
    )
    return llm.parse(
        stage="critique",
        model=MODEL_REASONING,
        schema=Critique,
        system=CRITIQUE_SYSTEM,
        user_content=_target_block(profile, size) + "\n\nPROPOSED BUYERS\n" + blocks,
        # Thinking and visible output share this budget, and a 12-match critique
        # at effort="high" does not fit in 8k once a long reasoning pass runs.
        max_tokens=16000,
        effort="high",
    )
