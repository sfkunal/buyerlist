# SMB → PE Buyer-List Tool — Build Plan

**Deadline:** 24 hours · **Scope:** broad across sectors · **Provider:** Anthropic API

---

## 1. Thesis

> Extracting a company profile from a website is the easy half. The hard half is that the LLM
> has **no reliable knowledge of the PE fund universe** — so the model must never be allowed to
> *name* a fund. It only ranks funds retrieved from an index we built.

Everything below follows from that constraint. It reframes the project from prompt engineering
into **retrieval and ranking against a measurable objective**, which is the register a
quantitative firm thinks in.

Three things separate this from the median submission:

1. **Matching happens at the portfolio-company level, not the fund level.** Real lower-middle-market
   deals happen because Fund X owns a regional roll-up that has done six tuck-ins in adjacent
   markets — not because "Fund X likes business services."
2. **It is backtested against real announced transactions,** with an explicit leakage guard.
3. **Company size is estimated from proxies with a stated range and rationale,** and that estimate
   gates the fund filter — so it is load-bearing, not decoration.

---

## 2. Stack & repo layout

Python 3.12 + `uv`. `httpx` + `trafilatura` for fetch/extract, `anthropic` SDK, SQLite for the
index, `rich` for the CLI. No vector DB, no agent framework, no web UI.

```
buyerlist/
  cli.py                 # run | index build | index qa | eval | reverse
  fetch.py               # sitemap → page scoring → targeted text extraction
  extract.py             # CompanyProfile (structured output)
  size.py                # revenue/EBITDA estimator from proxies
  index/
    discover.py          # web_search → fund universe
    build.py             # scrape fund site → FundProfile + platforms
    store.py             # SQLite + compact index rendering
    qa.py                # field-level accuracy eval on a 20-fund sample
  match.py               # hard filters → exhaustive LLM scoring
  critique.py            # adversarial pass + disqualifiers
  memo.py                # buyer-list memo (markdown + json)
  evalset/
    deals.jsonl          # held-out real transactions
    run_eval.py          # recall@k with leakage guard
  schemas.py             # Pydantic models, shared by extract + index
data/
  funds.sqlite           # COMMITTED — interviewer can clone and run
```

**Why no vector search.** With ~250 funds, hard filters cut to ~40 candidates and we score all of
them exhaustively in a single call. At this scale that is *better* than approximate retrieval, not
a shortcut — and it is a clean line in the demo. (Note: Anthropic does not ship an embeddings API,
so the alternative would mean a third-party dependency for no accuracy gain here.)

---

## 3. Cost model (revised)

The original estimate assumed 250 funds × ~15K tokens on `claude-opus-5`. Three fixes:

| Lever | Effect |
|---|---|
| **Targeted extraction** — feed only the criteria block + portfolio list, not whole pages | ~15K → ~3.5K tokens/fund (**4×**) |
| **`claude-haiku-4-5` for index extraction** ($1/$5 vs $5/$25) | **5×** |
| **Tiered depth** — deep pass on ~80 funds, shallow on ~170 | ~1.5× |

| Line item | Cost |
|---|---|
| Index build — deep tier, 80 funds (criteria + portfolio + platform pages) | ~$1.45 |
| Index build — shallow tier, 170 funds (criteria only) | ~$1.20 |
| Retries / failed fetches (+30%) | ~$0.80 |
| **Index build total** | **~$3.45** (≈$1.75 if run through the Batch API) |
| Dev iteration across 24h | ~$10 |
| Backtest, 30 deals | ~$6 |
| Demo runs | ~$1 |
| **Project total** | **under $25** |

The index is built **once** and committed to the repo, so the demo has zero marginal build cost.

### Model assignment

| Stage | Model | Why |
|---|---|---|
| Index extraction (both tiers) | `claude-haiku-4-5` | Mechanical extraction from explicit criteria pages — its sweet spot |
| Target company extraction | `claude-sonnet-5` | Messier, less structured source pages |
| Size estimation | `claude-opus-5` | Genuine proxy reasoning; one cheap call per run |
| Ranking + critique | `claude-opus-5` | Where the judgment lives, and cheap because the index prefix is cached |

Haiku 4.5 notes: no `effort` parameter (errors), no adaptive thinking, 200K context, 64K max
output. Plain `messages.parse()` calls with a JSON schema. Its minimum cacheable prefix is 4096
tokens, so the extraction system prompt likely won't cache — don't claim otherwise.

### Tiered depth is a design decision, not just a cut

- **Deep tier (~80 funds):** full `FundProfile` *plus* platform companies and their add-on history.
  This is what produces the add-on thesis that makes the output credible.
- **Shallow tier (~170 funds):** criteria only. Provides breadth so the backtest isn't scored
  against a universe hand-picked to contain the answers.

Say this out loud in the demo: *"I spent compute where it changes the answer."*

---

## 4. Schedule

| Block | Hours | Ships |
|---|---|---|
| 1. Skeleton + fetch layer | 0–2 | CLI stub, page-scoring crawler, graceful degradation for JS-only / one-page / PDF-brochure sites |
| 2. Extraction + schemas | 2–4 | `CompanyProfile` with per-claim evidence, NAICS mapping, `SizeEstimate` |
| 3. Fund discovery | 4–5 | ~250 LMM funds across sectors, homepages resolved, deduped, tier-assigned |
| 4. Index build | 5–8 | Parallel scrape + Haiku extraction → SQLite; **commit the artifact** |
| 5. Index QA eval | 8–9 | Field-level accuracy on a 20-fund hand-checked sample |
| 6. Matching + ranking | 9–12 | Hard filters, exhaustive cached scoring, critique pass |
| 7. Memo output | 12–14 | Tiered buyer list, outreach angle per fund, evidence links |
| 8. Backtest | 14–18 | 25–30 held-out deals, leakage guard, recall@k |
| 9. Harden + demo | 18–22 | 10 diverse URLs end to end, pre-cached demo cases, talk track |
| 10. Buffer | 22–24 | Because something will break |

Fire block 3's discovery pass into the background while still writing block 2 — it is network-bound.

---

## 5. Pipeline

```
0. FETCH      sitemap + heuristic page scoring → pull ~15 highest-value pages
              (home, about, services, locations, team, careers, news)
              targeted extraction, not whole-page dumps

1. EXTRACT    claude-sonnet-5, strict JSON schema → CompanyProfile
              every claim carries {quote, source_url}
              sector mapped to NAICS codes, not free text   ← the join key

2. SIZE       claude-opus-5 → revenue/EBITDA ranges + explicit rationale

3. RETRIEVE   hard filters over the index (pure Python, no LLM):
              EBITDA band overlap · NAICS overlap · geography · stated exclusions
              → ~30-60 candidates from 250

4. RANK       claude-opus-5, one call, index prompt-cached
              fixed rubric, structured output, no free-form fund names permitted

5. CRITIQUE   claude-opus-5 adversarial pass over the top ~12:
              argue against each, surface disqualifiers

6. RENDER     buyer-list memo: Tier 1 / Tier 2 / wildcards, outreach angle each

7. EVAL       recall@k on held-out real transactions
```

**The elegant part:** stages 0–1 build the fund index too. Point the extractor at an SMB and get a
`CompanyProfile`; point it at a PE firm and get a `FundProfile`. One engine, aimed both ways.

---

## 6. Schemas

### `CompanyProfile`

```python
class Evidence(BaseModel):
    field: str            # "hq.state", "locations_count"
    quote: str            # verbatim from the page
    source_url: str

class CompanyProfile(BaseModel):
    legal_name: str; dba: str | None
    hq_city: str | None; hq_state: str | None
    service_area: list[str]
    naics_codes: list[NaicsCode]       # code + label + confidence
    description: str
    products_services: list[str]
    end_markets: list[str]             # residential / commercial / municipal / industrial
    business_model: list[str]          # recurring, project, contract, field-service, distribution, mfg
    year_founded: int | None
    locations_count: int | None
    ownership_signals: list[str]       # founder-led, family-owned, already PE-backed, franchise
    risk_flags: list[str]              # owner dependence, customer concentration, cyclicality
    evidence: list[Evidence]
    extraction_confidence: Literal["high","medium","low"]
```

Flat `evidence` list rather than per-field wrappers — structured outputs don't support recursive
schemas, and a small schema compiles faster on first use.

### `SizeEstimate`

Separate call; different kind of reasoning. Inputs: the profile plus raw proxy signals the
extractor collected (team-page headcount, location count, fleet mentions, "serving X clients since
Y", open job postings, equipment lists, certifications, union/non-union language).

```python
class SizeEstimate(BaseModel):
    revenue_low_usd: int; revenue_high_usd: int
    ebitda_low_usd: int;  ebitda_high_usd: int
    margin_assumption_pct: float
    signals_used: list[str]         # "18 named employees on team page"
    comparable_basis: str           # "residential HVAC, ~$180-250k revenue/tech"
    confidence: Literal["high","medium","low"]
    caveats: list[str]
```

When confidence is `low`, widen the band **and** widen the downstream filter rather than guessing
narrow. "I could not determine revenue; here is an $8–14M range and the five signals behind it"
reads as competence. Confident fabrication reads as a toy.

### `FundProfile`

```python
class AddOn(BaseModel):
    name: str; announced: str | None; city: str | None; state: str | None

class Platform(BaseModel):
    name: str; sector: str; naics_codes: list[str]
    description: str; hq_state: str | None
    acquired_year: int | None
    add_ons: list[AddOn]              # ← deep tier only; the differentiator

class FundProfile(BaseModel):
    firm_name: str; website: str
    hq_city: str; hq_state: str; other_offices: list[str]
    control: Literal["control","minority","both"]
    deal_types: list[str]             # platform, add-on, recap, growth
    ebitda_min_usd: int | None; ebitda_max_usd: int | None
    ev_min_usd: int | None;     ev_max_usd: int | None
    naics_coverage: list[str]
    sector_themes: list[str]
    geographies: list[str]
    exclusions: list[str]
    latest_fund: str | None; latest_fund_size_usd: int | None
    platforms: list[Platform]
    depth: Literal["deep","shallow"]
    source_urls: list[str]
    scraped_at: str
```

---

## 7. Ranking stage

**Stage A — hard filters (pure Python):**
- EBITDA estimate band overlaps the fund's stated band (widening factor when size confidence is low)
- NAICS 2-digit overlap, or 4-digit for tight sectors
- Geography: fund footprint contains the target's state, or fund is explicitly national
- Not excluded by the fund's own stated exclusions

**Stage B — exhaustive scoring, one cached call.**

The compact fund index (~250 funds × ~60 tokens ≈ 15K tokens) sits in the system prompt behind a
`cache_control` breakpoint. It is byte-stable across every run, so after the first call it reads at
~0.1× — and `usage.cache_read_input_tokens` is a live number to show on screen.

```python
resp = client.messages.parse(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},
    system=[
        {"type": "text", "text": RUBRIC},                  # frozen
        {"type": "text", "text": COMPACT_INDEX,            # frozen, ~15K tok
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ],
    messages=[{"role": "user", "content": candidate_block + profile_block}],
    output_format=RankedMatches,
)
```

Rubric, 0–5 per dimension:

| Dimension | Weight | What earns a 5 |
|---|---|---|
| Sector fit | 25% | Fund owns or has bought in this exact NAICS |
| Size fit | 20% | Target EBITDA sits mid-band of the fund's stated range |
| Geographic fit | 15% | Fund or a platform operates in or adjacent to the target's market |
| **Add-on thesis** | **25%** | Named platform with matching roll-up pattern and geographic adjacency |
| Recent activity | 10% | Deals in this sector within 24 months |
| Deal-type fit | 5% | Control/minority and platform/add-on match likely seller intent |

Output per fund: `score`, `tier`, `rationale`, `angle`, `evidence[]`, `confidence`. The model **may
only reference funds and platforms present in the provided candidate block.** That constraint is
the whole thesis.

**Stage C — critique.** A second `claude-opus-5` call takes the top ~12 and argues *against* each:
size mismatch, geographic stretch, sector adjacency that doesn't hold, fund late in its investment
period. Findings become the "why this might not work" column — the most useful column for a banker.

### Output shape

Not a list — the deliverable a banker actually wants:

> **Tier 1 — Alpine Investors**, via platform **Apex Service Partners**. Four HVAC add-ons in IL/IN
> since 2023, all $2–6M EBITDA, all founder-owned. You are the geographically adjacent OH market.
> Angle: add-on, not platform. *Risk: their last two tuck-ins were >$5M EBITDA; you may be below
> their current threshold.*

---

## 8. Two evals

### 8a. Index accuracy (block 5, ~$0 marginal)

Hand-check 20 funds against their source pages. Report field-level accuracy on the load-bearing
fields: `ebitda_min/max`, `control`, `geographies`, `naics_coverage`, and — for deep-tier funds —
platform names and add-on counts. This is the honest answer to "how do you know the Haiku
extraction is good enough?", and it costs one hour.

### 8b. Buyer recall (block 8)

For each held-out deal, feed the tool the **target's** website and check where the actual acquirer
lands.

```
recall@5  /  recall@10  /  recall@20
same-thesis-competitor@10        ← acquirer's close comp surfaced
median rank of the true acquirer
```

**The leakage guard.** The acquiring fund's portfolio page *now* lists the target you're testing
on — matching on that is cheating. Before scoring each case, filter the index snapshot to drop:

1. The target company itself from any platform's `add_ons` list
2. Any add-on announced **after** the deal date

So the tool matches on the acquirer's pattern *as it stood before the deal*. Build it, then say so
— this is the single most interviewer-proof detail in the project.

### Eval set — `evalset/deals.jsonl`

```json
{"acquirer_fund":"...","platform":"...","target":"...","target_url":"https://...",
 "announced":"2025-03","sector":"...","state":"..","source_url":"https://..."}
```

Sourced with the server-side web search tool (`web_search_20260209`): announced LMM acquisitions
from 2024–2025 where the target's website still resolves. 25–30 is plenty.

---

## 9. Telemetry

The CLI prints per run: wall-clock seconds, input/output tokens by stage, `cache_read_input_tokens`
vs `cache_creation_input_tokens`, and dollar cost. Now that a run costs roughly **$0.11 and ~40
seconds**, the comparison to ~4 hours of associate work is a strong closing line rather than a
hand-wave.

---

## 10. Demo, 12 minutes

1. **(1 min) Thesis + the failure.** Run the naive version live — one prompt, "which PE funds would
   buy this HVAC company." Watch it name Blackstone and a fund that doesn't exist. Then: *"So the
   model never names a fund. It only ranks funds I indexed."*
2. **(4 min) Full run, pre-cached.** Profile with evidence → size band with its reasoning → filter
   250→40 → ranked memo. Narrate the add-on thesis on the #1 match.
3. **(2 min) Live URL from the interviewer.** Built to degrade gracefully.
4. **(2 min) Both eval numbers.** Index accuracy, then buyer recall with the leakage guard explained.
5. **(2 min) One honest failure** you haven't fixed.
6. **(1 min) Roadmap slide** — bidirectional search (paste a *fund's* URL, get target SMBs),
   sell-side readiness flags, propensity-to-sell scoring, SEC Form ADV–anchored index.

---

## 11. Cut lines, in order

If block 8 runs late → 15 deals, not zero. Fifteen *with* the leakage guard beats thirty without.
If block 4 runs late → 150 funds, and **say so in the demo** rather than implying full coverage.
If block 5 runs late → 10 spot-checked funds instead of 20.

**Never cut:** evidence tracing, the size estimator, the critique pass, the leakage guard.

---

## 12. Housekeeping

- Respect `robots.txt`; cap concurrency at ~8 and set a descriptive User-Agent. Fund and SMB sites
  are small and easy to hammer.
- Cache every fetched page to disk so re-runs during development cost nothing.
- Commit `data/funds.sqlite` — the interviewer should be able to clone and run.
- Batch API (`client.messages.batches.create`, 50% discount) is optional for the index build.
  Batches typically finish in under an hour but are allowed up to 24; given the deadline, fire it
  early with a synchronous fallback, or just skip it — the savings are ~$1.70.
