"""Scrape each seeded PE firm's own site into a FundProfile.

Depth is tiered. Deep-tier funds get more pages and are asked for full platform
and add-on history, because that is what makes an add-on thesis possible.
Shallow-tier funds get criteria only, which is enough to be a legitimate
candidate and keeps the universe broad enough that the backtest is not scored
against a hand-picked set.

Everything is resumable: the build skips domains already in the database, so a
failure partway through a 250-site run costs only the remainder.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from ..fetch import fetch_site_sync
from ..llm import LLM, MODEL_INDEX_EXTRACT
from ..schemas import FundProfile
from .discover import SEED_PATH
from .store import connect, existing_domains, log_build, stats, upsert_fund

DEEP_TIER_TARGET = 80
DEEP_PAGES = 12
SHALLOW_PAGES = 5

FUND_SYSTEM_DEEP = """\
You extract a private equity firm's investment criteria and portfolio from its own website, \
for a tool that matches small businesses to plausible acquirers.

Rules:
- Only state what the pages support. Unstated numeric fields must be null, not guessed. A firm \
that does not publish an EBITDA range should have null, not a plausible-sounding range.
- ebitda_min_usd / ev_min_usd and friends are in whole US dollars: $5 million is 5000000.
- naics_coverage: map their stated sectors to NAICS codes. This is the join key used to match \
target companies, so prefer specific codes and include broader parents. Codes only, as strings.
- sector_themes: their own words, verbatim. Do not normalize away their language.
- exclusions: what they explicitly say they do NOT do.
- control: "control" for buyouts/majority, "minority" for non-control only, "both" if they do both, \
"unknown" if the site does not say.

PORTFOLIO — this part matters most, and NAICS accuracy here matters more than anywhere else.

- Record every portfolio company you can find, one entry each.
- naics_codes must describe WHAT THE COMPANY ACTUALLY DOES, not the firm's own sector label. \
A portfolio table often has a generic category column ("Services", "Industrials") next to a \
specific vertical column ("HVAC Plumbing & Electrical", "Dental", "Auto Glass"). THE SPECIFIC \
VERTICAL WINS. An HVAC and plumbing roll-up is 238220 (Plumbing, Heating and Air-Conditioning \
Contractors) — it is NOT 561720 janitorial or 561790 other building services, even if the firm \
files it under "Services". Getting this wrong makes the company unmatchable, so read the vertical \
column and map from the actual line of business.
- role: set "add_on" when the site labels the row an add-on or tuck-in, "platform" when it labels \
it a platform investment, "unknown" when it says neither.
- parent_platform: only if the site actually names which platform an add-on went into. Do not \
guess a parent from sector similarity.
- add_ons: nest acquisitions here when the site explicitly ties them to this platform (common on \
news and press pages). Capture name, month (YYYY-MM) and city/state when shown.

Missing data is fine; invented data is not.
"""

FUND_SYSTEM_SHALLOW = """\
You extract a private equity firm's investment criteria from its own website, for a tool that \
matches small businesses to plausible acquirers.

Rules:
- Only state what the pages support. Unstated numeric fields must be null, not guessed.
- ebitda_min_usd / ev_min_usd and friends are in whole US dollars: $5 million is 5000000.
- naics_coverage: map their stated sectors to NAICS codes (strings). This is the matching key.
- sector_themes: their own words, verbatim.
- exclusions: what they explicitly say they do NOT do.
- control: "control", "minority", "both", or "unknown" if unstated.
- For platforms, list portfolio company names and sectors if a portfolio page was fetched. Leave \
add_ons empty unless the site explicitly identifies add-on acquisitions.
- naics_codes on a portfolio company must describe what that company actually does, taken from \
the most specific vertical the page gives — not the firm's generic category label. An HVAC and \
plumbing business is 238220, not a generic building-services code.
- role: "add_on" / "platform" when the site says so, otherwise "unknown". parent_platform only if \
the site names the parent.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_one(llm: LLM, seed: dict, tier: str) -> tuple[FundProfile | None, int, str]:
    """Fetch and extract one firm. Returns (profile, pages_fetched, note)."""
    max_pages = DEEP_PAGES if tier == "deep" else SHALLOW_PAGES
    snap = fetch_site_sync(seed["website"], profile="fund", max_pages=max_pages)

    if not snap.pages:
        return None, 0, "; ".join(snap.notes) or "no pages fetched"

    content = (
        f"Firm (from discovery, may be imprecise): {seed['firm_name']}\n"
        f"Website: {snap.root_url}\n\n"
        f"{snap.to_prompt_text(max_chars=40_000 if tier == 'deep' else 14_000)}"
    )
    profile = llm.parse(
        stage=f"index.extract.{tier}",
        model=MODEL_INDEX_EXTRACT,
        schema=FundProfile,
        system=FUND_SYSTEM_DEEP if tier == "deep" else FUND_SYSTEM_SHALLOW,
        user_content=content,
        max_tokens=8000 if tier == "deep" else 4000,
    )
    # The scraped site is authoritative for the URL; discovery's name is a hint.
    profile.website = snap.root_url
    return profile, len(snap.pages), ""


def build(llm: LLM, limit: int | None = None, verbose: bool = True) -> dict:
    if not SEED_PATH.exists():
        raise RuntimeError(f"No seed file at {SEED_PATH}. Run discovery first.")

    seeds = json.loads(SEED_PATH.read_text())
    conn = connect()
    done = existing_domains(conn)

    todo = [s for s in seeds if s["domain"] not in done]
    if limit:
        todo = todo[:limit]

    if verbose:
        print(f"{len(seeds)} seeded, {len(done)} already built, {len(todo)} to do")

    deep_used = sum(1 for r in conn.execute("SELECT tier FROM funds WHERE tier='deep'"))

    for i, seed in enumerate(todo, 1):
        tier = "deep" if deep_used < DEEP_TIER_TARGET else "shallow"
        t0 = time.time()
        try:
            profile, pages, note = build_one(llm, seed, tier)
        except Exception as e:
            profile, pages, note = None, 0, f"{type(e).__name__}: {e}"

        if profile is None:
            log_build(conn, seed["domain"], False, note, _now())
            if verbose:
                print(f"  [{i}/{len(todo)}] {seed['domain'][:34]:36} SKIP  {note[:52]}")
            continue

        upsert_fund(
            conn,
            domain=seed["domain"],
            tier=tier,
            discovered_via=seed.get("discovered_via"),
            profile=profile,
            scraped_at=_now(),
            pages_fetched=pages,
        )
        log_build(conn, seed["domain"], True, "", _now())
        if tier == "deep":
            deep_used += 1

        if verbose:
            n_plat = len(profile.platforms)
            n_add = sum(len(p.add_ons) for p in profile.platforms)
            def _m(v: int | None) -> str:
                return f"{v // 1_000_000}M" if v else "?"

            if profile.ebitda_min_usd or profile.ebitda_max_usd:
                band = f"{_m(profile.ebitda_min_usd)}-{_m(profile.ebitda_max_usd)}"
            else:
                band = "no band"
            print(
                f"  [{i}/{len(todo)}] {seed['domain'][:34]:36} {tier:8} "
                f"{pages:>2}p  {n_plat:>3} plat {n_add:>3} add  {band:>10}  "
                f"{time.time()-t0:>4.0f}s  (${llm.telemetry.total_cost_usd:.2f})"
            )

    s = stats(conn)
    conn.close()
    return s


if __name__ == "__main__":
    llm = LLM()
    s = build(llm)
    print(f"\nindex: {s}")
    print(f"cost ${llm.telemetry.total_cost_usd:.2f}  time {llm.telemetry.total_seconds:.0f}s")
