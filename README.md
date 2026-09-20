# buyerlist

Give it the website of a small or medium-sized business and it returns the memo an M&A analyst
would write: which private equity firms would plausibly buy this company, which portfolio platform
they would buy it into, how to open the conversation, and the strongest honest case against each
match. The pipeline reads the site, extracts a structured company profile with every claim tied to
a verbatim quote and URL, estimates revenue and EBITDA from proxies, filters a verified index of PE
funds down to real candidates, and scores the survivors against a fixed rubric. The design
constraint that shapes everything: **the model is never allowed to name a fund - it may only rank
funds retrieved from an index that is built and verified.** See `SPEC.md` for the full argument and
`PLAN.md` for the build plan.

## Install

Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

That puts a `buyerlist` command on your PATH. If you would rather not install the package, the
dependency list is also available as a plain requirements file:

```bash
pip install -r requirements.txt
```

with which you invoke the tool as `python -m buyerlist` instead of `buyerlist`.

Run every command from the repository root. Paths to the index, the fetch cache, and the output
directory are resolved relative to the working directory.

## Credentials

```bash
cp .env.example .env
```

Then edit `.env` and set `ANTHROPIC_API_KEY` to your key. `.env` is gitignored. The package loads it
on import, so any entry point picks it up. A real environment variable always wins over the file, so
`ANTHROPIC_API_KEY=... buyerlist run ...` works too.

## Usage

Generate a buyer list for a company:

```bash
buyerlist run https://www.hoffmannbros.com
buyerlist run https://www.hoffmannbros.com --max-pages 20   # default is 12
```

It prints the extracted profile, the size estimate, the filter funnel, and a ranked table, then
writes a markdown memo and a JSON payload to `out/`.

Inspect the fund index:

```bash
buyerlist index stats
```

Rebuild the index from the seed list (this makes a lot of model calls and costs money — the built
index is committed, so you do not need to):

```bash
buyerlist index build
buyerlist index build --limit 5    # a few funds only, for a smoke test
```

Index QA. `check` is an automated consistency scan over the whole index and makes no model calls;
`sample` writes a worksheet for grading fields by hand against the source pages:

```bash
python -m buyerlist.index.qa check                  # writes out/index_qa_issues.json
python -m buyerlist.index.qa check --fail-on-error  # exit 1 if any error-severity issue
python -m buyerlist.index.qa sample --n 20 --seed 7 # writes out/index_qa.md
```

Backtest. The harness measures recall@k against real historical acquisitions, with a leakage guard
that strips the target from the acquirer's portfolio and drops add-ons announced after the deal, so
the tool only ever sees the acquirer's pattern as it stood beforehand:

```bash
python -m buyerlist.evalset.run_eval --self-test   # offline guard self-tests, no API key needed
python -m buyerlist.evalset.run_eval --dry-run     # leakage guard + coverage only, no model calls
python -m buyerlist.evalset.run_eval               # full scoring; writes out/backtest.json
python -m buyerlist.evalset.run_eval --limit 5     # score the first N deals only
```

**Status:** the backtest has not been run. It reads its deal set from
`buyerlist/evalset/deals.jsonl`, which is not in the repository yet; without it the harness reports
that there is nothing to score and exits. There are no recall numbers, and none are claimed. The
same goes for the hand-graded index QA sample — the harness is there to run, the results are not.

## How it works

Seven stages, each doing one thing, with the expensive model used only where judgment is actually
required:

| Stage | What happens |
|---|---|
| `FETCH` | sitemap plus heuristic page scoring picks the ~12 pages that carry signal |
| `EXTRACT` | a `CompanyProfile`, every claim tied to a verbatim quote and its URL |
| `SIZE` | revenue and EBITDA range inferred from proxies, with the reasoning exposed |
| `RETRIEVE` | hard filters over the fund index — pure Python, no model |
| `RANK` | one cached call scores every survivor against a fixed rubric |
| `CRITIQUE` | an adversarial pass that argues against each match |
| `RENDER` | a tiered buyer memo with provenance and run cost |

The matching unit is the **portfolio platform and its acquisition history**, not the fund — deals
happen because a sponsor owns a regional roll-up that wants a seventh tuck-in, not because a fund
"likes business services."

The no-hallucination rule is enforced twice. The ranking prompt forbids naming any firm outside the
candidate list, and then the pipeline drops any returned firm name that is not in the candidate set
and reports what it dropped. It is a runtime check with an observable failure count, not a hopeful
instruction.

The same crawler and extractor are pointed both ways: at an SMB's site they produce a
`CompanyProfile`, at a sponsor's site a `FundProfile`. Only the page-scoring keyword profile
differs.

## The fund index

`data/funds.sqlite` is committed, so the tool runs immediately after install — you never need to
rebuild it. Each entry is scraped from the firm's own website: investment criteria, EBITDA and
enterprise value bands, geography, stated exclusions, and the full portfolio with the
platform/add-on structure preserved. Nothing enters on the model's say-so: the homepage has to
resolve, the site has to yield real scraped criteria, and a sample is hand-checked field by field.

`data/cache/` (the fetch cache) and `out/` are gitignored; both are recreated as needed.
