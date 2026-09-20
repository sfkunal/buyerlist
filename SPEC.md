# Buyer-List Engine — Product Spec

**What it does:** you give it the website of a small or medium-sized business. It gives you back
the memo an M&A analyst would write — who would plausibly buy this company, why, how to open the
conversation, and why each match might not work.

One command, ~40 seconds, ~$0.12.

```
$ buyerlist run https://www.hoffmannbros.com
```

---

## 1. The problem, stated precisely

Building a buyer list for a founder-owned business is a few hours of associate work: read the
company's site, figure out what it actually does and roughly how big it is, then work out which
private equity firms have a live thesis that fits.

The obvious way to automate this is to ask a language model. **That approach fails, and it fails in
a specific way that is worth understanding, because avoiding it is the entire design.**

Ask a model "which PE funds would buy this HVAC company in Ohio" and it will name Blackstone, KKR,
and one or two firms that do not exist. It is not being careless. Naming mid-market PE firms and
their portfolio companies is a *recall* task over a long tail of thinly-documented private
entities — precisely the task language models are least reliable at. The answers are fluent,
specific, plausible, and unusable.

So the central design constraint:

> **The model is never allowed to name a fund. It may only rank funds retrieved from an index we
> built and verified.**

Everything else follows from that one line.

---

## 2. What was built

A seven-stage pipeline. Each stage does one thing, and the expensive model is used only where
judgment is actually required.

```
FETCH      sitemap + heuristic page scoring -> the ~12 pages that carry signal
EXTRACT    -> CompanyProfile, every claim tied to a verbatim quote and URL
SIZE       -> revenue/EBITDA range inferred from proxies, with stated reasoning
RETRIEVE   hard filters over the fund index (pure Python, no model)
RANK       one cached call scores every survivor against a fixed rubric
CRITIQUE   an adversarial pass that argues against each match
RENDER     tiered buyer memo, with provenance and run cost
```

### The asset: a verified fund index

The index is the thing that makes the tool work, and it is built once and committed to the repo.

| | |
|---|---|
| Funds | ~178 verified firms |
| Portfolio companies | 1,000+ platforms |
| **Add-on acquisitions** | 255 recorded — but only ~66% carry a date, and only a subset are linked to a named parent platform |
| Cost to build | ~$0.023 per fund (~$2.50 total) |

That add-on caveat is deliberate. An earlier version of this document claimed "235+ add-ons"
without qualification; that figure counted add-on rows that had been parsed as standalone
platforms, so it overstated usable add-on *linkage*. Many firms list tuck-ins without naming which
platform they went into, and the extractor is instructed not to guess a parent from sector
similarity. The honest read: add-on history is rich enough to drive real theses, and thinner than
a raw count suggests.

Each entry is scraped from the firm's **own website** — investment criteria, EBITDA and enterprise
value bands, geography, stated exclusions, and the full portfolio with platform/add-on structure
preserved.

**Nothing enters the index on the model's say-so.** Three filters stand in the way, and no invented
firm survives them:

1. **The homepage must resolve.** This is not theoretical: of 101 seeded firms, **21 were dropped
   here** — wrong or dead domains.
2. **The site must yield real scraped criteria.** A name with no investment-criteria page does not
   become an index entry.
3. **A hand-checked sample** is graded field-by-field against source pages (see §5).

### The same engine, pointed both ways

The crawler and extractor that read an SMB's website are the same ones that read a PE firm's
website. One `PROFILES` table holds the page-scoring keywords for each direction; nothing else
differs. Point it at a plumbing company and get a `CompanyProfile`; point it at a sponsor and get a
`FundProfile`.

---

## 3. What makes it different

### 3a. It matches at the portfolio-company level, not the fund level

This is the core insight, and it is where most of the value sits.

Deals in this market do not happen because a fund "likes business services." They happen because a
fund owns a regional roll-up that has done six tuck-ins in adjacent markets and wants a seventh.
So the matching unit is the **platform company and its acquisition history**, not the fund.

Compare the two outputs:

> ❌ *Alpine Investors — they invest in services businesses.*

> ✅ *Alpine Investors, via platform **Apex Service Partners** — four HVAC add-ons in IL/IN since
> 2023, all $2–6M EBITDA, all founder-owned. You are the geographically adjacent OH market.
> Angle: add-on, not platform.*

The second is a thesis an analyst can act on. It requires knowing the platform exists, what it has
been buying, and where — which is why the index carries 235+ add-ons with geography attached, and
why `addon_thesis` is the single highest-weighted scoring dimension at 25%.

### 3b. It estimates size from proxies, and says how

Websites never publish revenue. But they leak: headcount on a team page, number of locations,
"serving 500+ clients since 1978", fleet photos, open roles, listed equipment.

A dedicated stage reads those proxies and produces a **range with its reasoning exposed** — the
benchmark applied, the signals used, the assumed margin, and what would most change the answer.
Real output from a live run:

> Revenue **$48M–$95M**, EBITDA **$4.8M–$14M** at an assumed 12% margin, confidence *medium*.
> *Basis:* multi-trade residential/light-commercial home services run $150–220k of revenue per
> total employee; at 300+ employees with an estimated 55–65% billable, that frames a mid-$50M to
> high-$80M business. *Caveat:* service-and-replacement mix versus commercial project work is the
> single biggest margin driver — pure residential demand-service runs 15–20% EBITDA, commercial
> project work 5–8%.

That is a defensible analyst-grade estimate, not a guess dressed as a number. And it is
**load-bearing**: the estimate gates which funds are even considered.

### 3c. Every claim is traceable

Each field in the company profile carries a verbatim quote and the URL it came from. The memo ends
with a provenance section. A quote that merely sits *near* a fact does not count — if the extractor
cannot find text containing the fact, the field is null rather than inferred.

### 3d. It argues against itself

A separate adversarial pass takes the top matches and makes the strongest honest case *against*
each one: size mismatch, geographic stretch dressed up as adjacency, a platform that has stopped
acquiring, a minority investor for a seller who wants out. Findings appear inline in the memo.

For a banker, "why this might not work" is the most useful column on the page.

### 3e. The no-hallucination rule is enforced in code, not just prompted

The ranking prompt forbids naming any firm outside the candidate list. That is necessary but not
sufficient — so after ranking, the pipeline **drops any firm name not in the candidate set and
reports what it dropped**. The rule is a runtime check with an observable failure count, not a
hopeful instruction.

### 3f. It reports its own cost

Every run prints tokens, cache hit rates, and dollars by stage. The fund index sits behind a prompt
cache breakpoint because it is byte-identical on every run, so it bills at roughly a tenth of input
rate after the first call.

This turns the value claim into a measurement: **~$0.12 and ~40 seconds, versus roughly four hours
of associate time.**

---

## 4. Engineering decisions worth defending

**No vector database.** At 80 funds, hard filters cut to a few dozen candidates and the model
scores all of them exhaustively in one call. At this scale exhaustive scoring beats approximate
retrieval outright, and it removes an embedding dependency entirely. The scaling path (embeddings
past ~5k funds) is understood; it just is not needed yet.

**NAICS codes as the join key.** Sectors are matched on NAICS, not free text — otherwise "HVAC"
fails to match a fund that wrote "mechanical services."

**Cheapest model that does the job, per stage.** Mechanical extraction from explicit criteria pages
runs on Haiku 4.5; messier SMB sites on Sonnet 5; only size reasoning, ranking, and critique use
Opus 5. This cut the index build from an estimated $30–50 to roughly **$2**.

**The filter excludes only on positive evidence of mismatch.** Discovered from the data: only
**24 of 52 funds publish an EBITDA band at all**. Treating a missing band as a failed test would
have silently deleted half the universe. Unknown values pass through to be judged at ranking.

---

## 5. Honest status

Built and validated:

- Fetch, extraction, and size estimation — validated end to end on live sites at $0.1225 / 41s
- Fund index — ~178 firms, 1,000+ platforms
- Matching, ranking, critique, memo, CLI — run end to end on live targets
- Index QA eval — automated consistency checks over the whole index, 82% of entries clean
- Backtest harness — leakage guard and recall@k implemented, with offline self-tests

There is **no committed test suite** (no pytest, no `tests/`). An earlier draft of this document
said "filter logic unit-tested", which overstated it: the filter primitives were checked with
ad-hoc assertions during development, and the only tests that live in the repo are the offline
self-tests inside `run_eval.py` and the QA checks in `index/qa.py`.

### Backtest results

The tool was pointed at the websites of **54 companies that were actually acquired** in 2024–26,
each traced to a real announcement. Before scoring a deal, a leakage guard rebuilds the index as
it stood *before* that deal: it removes the target from any firm's portfolio, drops add-ons
announced after the deal month, and drops platforms acquired after the deal year. Across the run
it stripped **2,182 index entries** — including Siltworm sitting inside Align Capital's own
portfolio page, which would otherwise have been a free win.

Against a **170-fund index**, on the 18 deals whose acquirer was present:

| metric | value |
|---|---|
| recall@1 | **4 / 18 (22%)** |
| recall@5 | **7 / 18 (39%)** |
| recall@10 | **8 / 18 (44%)** |
| recall@20 | 8 / 18 (44%) |
| median rank, when found | **1.5** |
| deals errored | 0 |
| cost | $11.34 |

Four deals put the actual acquirer **first**. Ten of eighteen were misses.

**Coverage, not ranking, is the bottleneck.** Across the full 54-deal set only **21 of 54 (39%)**
of the real acquirers existed in the index at all. Those two numbers measure different things —
ranking quality versus index breadth — and they fail in different ways, so reporting a single
blended recall would have pointed the next week of work at the scoring rubric when the evidence
points squarely at index coverage.

**recall@10 equals recall@20.** Nothing was found between ranks 11 and 20, which suggests misses
are hard-filter losses rather than the ranker burying a candidate it actually saw.

#### What these numbers do not show

- **n = 18 is small.** The 95% interval on recall@10 is roughly ±0.23. Directional, not precise.
- **"The actual acquirer" is a flawed target, and it biases against the tool.** A buyer list is not
  trying to predict the single winner; it is trying to produce ten credible buyers so a banker can
  run a process. If the tool surfaces ten plausible funds and the business sells to the eleventh on
  price or chemistry, recall records a miss on a list that was fine. Treat 44% as a **lower bound**
  on list quality. Measuring the actual product would need blind professional rating against a
  baseline — human judgement, not a backtest.
- **The eval set has survivorship bias.** The largest drop category while building it was dead or
  parked target websites — companies absorbed post-acquisition whose sites now redirect to the
  acquirer. The set therefore skews toward targets that kept operating independently.

Known limits:

- The universe is **170 funds, not the 250 originally planned**. Universe size is printed next to
  every recall figure, because a recall number without it is close to meaningless.
- Sites that are JavaScript-rendered, image-only, or that opt out via robots.txt degrade
  gracefully and say so rather than silently returning a thin profile.

---

## 6. Where it goes next

- **Bidirectional search** — paste a *fund's* URL, get a list of SMB targets. The index is already
  symmetric; only the query direction changes.
- **Sell-side readiness flags** — owner dependence, customer concentration, no recurring revenue.
  Partially captured already as `risk_flags`.
- **Propensity to sell** — "family owned since 1978", a recent CFO hire, no visible succession.
- **SEC Form ADV as an index anchor** — a real regulatory universe of firms with private-fund AUM,
  rather than a curated seed list.
