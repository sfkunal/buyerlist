"""Build the seed universe of lower-middle-market PE firms.

Discovery runs in two steps per query rather than one. Step one uses the
server-side web_search tool and returns prose; step two converts that prose to
schema on a cheap model. Doing it this way keeps server-tool `pause_turn`
handling away from the structured-output path, and the second call costs
fractions of a cent.

Every discovered URL is verified to resolve before it enters the seed file. The
model is being asked to recall firm names, which is exactly the kind of recall
it is unreliable at — so nothing enters the index on the model's say-so alone.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from ..fetch import USER_AGENT
from ..llm import LLM, MODEL_COMPANY_EXTRACT, MODEL_INDEX_EXTRACT

# Discovery only needs to tell a real sponsor from an advisory/aggregator site,
# and every URL is verified downstream, so this does not need the top tier.
MODEL_SEARCH = MODEL_COMPANY_EXTRACT

SEED_PATH = Path("data/fund_seeds.json")

# Broad sector coverage. The brief asks for breadth across sectors, so the
# universe has to span them rather than going deep in one vertical.
SECTORS = [
    "HVAC, plumbing and electrical home services",
    "commercial landscaping and facilities services",
    "specialty and niche industrial manufacturing",
    "value-added distribution",
    "healthcare services (dental, veterinary, behavioral health)",
    "accounting, insurance brokerage and professional services",
    "IT managed services providers and vertical software",
    "transportation, trucking and third-party logistics",
    "food and beverage manufacturing",
    "aerospace and defense components",
    "building products and materials",
    "environmental, waste and remediation services",
    "automotive aftermarket and collision repair",
    "specialty construction and infrastructure services",
    "education, training and childcare services",
    "consumer products and direct-to-consumer brands",
    "pest control and residential recurring services",
    "testing, inspection and certification",
    "packaging and printing",
    "marine, RV and powersports dealers",
    "security, fire and life safety services",
    "metal fabrication and precision machining",
    "pharma services and contract manufacturing",
    "telecom and utility infrastructure services",
    "agriculture and food supply chain services",
]


class FundCandidate(BaseModel):
    firm_name: str
    website: str = Field(description="Homepage URL including scheme.")
    hq_hint: str | None = Field(description="City, State if mentioned, else null.")
    why_lmm: str = Field(
        description="The specific evidence this is a lower-middle-market PE firm, "
        "e.g. a stated EBITDA range or check size."
    )


class FundCandidates(BaseModel):
    candidates: list[FundCandidate]


SEARCH_PROMPT = """\
Find private equity firms whose websites you can confirm, that buy companies in: {sector}

I only need the firm NAME and HOMEPAGE URL. Do not research their investment criteria, \
headquarters, or portfolio — that gets scraped from their own site later. Spend your searches on \
finding more firms, not on detail about any one firm.

What counts as in scope — the test is the size of the companies they BUY, not the size of the \
fund. Include:
- Dedicated lower-middle-market sponsors (buying roughly $1M-$25M EBITDA businesses)
- Larger sponsors that run buy-and-build platforms acquiring small founder-owned companies. \
A multi-billion-dollar fund whose roll-up platform does $2M-EBITDA tuck-ins is very much in scope, \
because it is a real buyer for a business this size.

Exclude only:
- Venture capital and growth-equity-only investors
- Search funds, independent sponsors without committed capital, and business brokers
- Private credit and mezzanine lenders
- Investment banks, M&A advisors, and deal aggregator/marketplace sites (Axial, PrivSource and \
similar are not buyers)

Return 8-10 firms as a simple list of "Firm Name — https://homepage". Only list firms whose \
website appeared in a search result. Do not supply a URL from memory.
"""

STRUCTURE_PROMPT = """\
Convert the following research notes about private equity firms into structured records.

Copy firm names and URLs exactly as they appear in the notes. Do not add firms that are not in \
the notes, and do not correct or complete a URL from your own knowledge.

NOTES:
{notes}
"""


def _search_one(llm: LLM, sector: str) -> str:
    """Run one web-search-backed query, resuming if the server tool pauses."""
    messages = [{"role": "user", "content": SEARCH_PROMPT.format(sector=sector)}]
    # 6 was too few: the model spent them locating firms and had none left to
    # confirm URLs, then correctly refused to invent any. Narrowing the ask to
    # name+URL and raising the cap lets a sector complete in one pass.
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]

    transcript: list[str] = []
    deadline = time.time() + 180  # a sector is never worth more than 3 minutes

    for _ in range(3):  # bounded pause_turn resumption
        t0 = time.time()
        resp = llm.client.messages.create(
            model=MODEL_SEARCH,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            tools=tools,
            messages=messages,
        )
        llm.telemetry.record("discover.search", MODEL_SEARCH, resp.usage, time.time() - t0)

        transcript.extend(b.text for b in resp.content if b.type == "text")

        if resp.stop_reason != "pause_turn":
            break

        # Append the paused turn rather than replacing history. Dropping the
        # prior assistant turn makes the model lose its completed searches and
        # start over, which turns a pause into an effectively endless re-search.
        messages = messages + [{"role": "assistant", "content": resp.content}]
        if time.time() > deadline:
            break

    return "\n".join(transcript)


def _structure(llm: LLM, notes: str) -> list[FundCandidate]:
    if not notes.strip():
        return []
    out = llm.parse(
        stage="discover.structure",
        model=MODEL_INDEX_EXTRACT,
        schema=FundCandidates,
        user_content=STRUCTURE_PROMPT.format(notes=notes[:30_000]),
        max_tokens=6000,
    )
    return out.candidates


def _domain(url: str) -> str:
    host = urlparse(url if url.startswith("http") else "https://" + url).netloc.lower()
    return host.removeprefix("www.")


async def _verify(candidates: list[FundCandidate]) -> list[dict]:
    """Drop anything whose homepage does not actually resolve.

    This is the guard against the model recalling a plausible-sounding firm or
    inventing a URL. A firm that fails here never reaches the index.
    """
    sem = asyncio.Semaphore(8)
    headers = {"User-Agent": USER_AGENT}

    async def check(c: FundCandidate) -> dict | None:
        url = c.website if c.website.startswith("http") else "https://" + c.website
        async with sem:
            try:
                async with httpx.AsyncClient(
                    headers=headers, follow_redirects=True, timeout=15.0
                ) as client:
                    r = await client.get(url)
                    if r.status_code >= 400:
                        return None
                    return {
                        "firm_name": c.firm_name,
                        "website": str(r.url).rstrip("/"),
                        "domain": _domain(str(r.url)),
                        "hq_hint": c.hq_hint,
                        "why_lmm": c.why_lmm,
                        "status": r.status_code,
                    }
            except Exception:
                return None

    results = await asyncio.gather(*[check(c) for c in candidates])
    return [r for r in results if r]


def discover(llm: LLM, sectors: list[str] | None = None, verbose: bool = True) -> list[dict]:
    sectors = sectors or SECTORS
    seen: dict[str, dict] = {}

    # Resume support: a 25-query run is long enough that losing it to one
    # transient failure would hurt.
    if SEED_PATH.exists():
        for row in json.loads(SEED_PATH.read_text()):
            seen[row["domain"]] = row
        if verbose:
            print(f"resuming with {len(seen)} firms already seeded")

    for i, sector in enumerate(sectors, 1):
        try:
            notes = _search_one(llm, sector)
            cands = _structure(llm, notes)
            verified = asyncio.run(_verify(cands))
        except Exception as e:
            print(f"  [{i}/{len(sectors)}] {sector[:44]:46} FAILED: {type(e).__name__}: {e}")
            continue

        added = 0
        for row in verified:
            if row["domain"] in seen:
                continue
            row["discovered_via"] = sector
            seen[row["domain"]] = row
            added += 1

        if verbose:
            print(
                f"  [{i}/{len(sectors)}] {sector[:44]:46} "
                f"found {len(cands):>2} verified {len(verified):>2} new {added:>2} "
                f"total {len(seen):>3}  (${llm.telemetry.total_cost_usd:.2f})"
            )

        SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEED_PATH.write_text(json.dumps(list(seen.values()), indent=2))

    return list(seen.values())


if __name__ == "__main__":
    llm = LLM()
    rows = discover(llm)
    print(f"\ndiscovered {len(rows)} verified firms -> {SEED_PATH}")
    print(f"cost ${llm.telemetry.total_cost_usd:.2f}  time {llm.telemetry.total_seconds:.0f}s")
