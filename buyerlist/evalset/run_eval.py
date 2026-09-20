"""Backtest the buyer list against deals that actually happened.

The tool's claim is "these firms might buy this company". The only honest way to
test that claim is to point it at businesses that were already bought, hide the
outcome, and ask where the real acquirer landed in the ranking.

Hiding the outcome is the hard part, and it is the reason this file exists at
all. The fund index is scraped from portfolio pages *today*. The acquirer's
portfolio page now lists the very company we are testing on — often with the
sector, the state, and the year attached. Rank against that index and the model
is not predicting a buyer, it is reading the answer off the page. Every recall
number produced that way is fiction.

So before a deal is scored, `guard_index` rebuilds a view of the index as it
looked the month *before* the deal was announced: the target is deleted wherever
it appears, and anything the index knows only because time passed (add-ons
announced later, platforms acquired in later years) is deleted with it. The
guard is a pure function over loaded rows — the SQLite file is opened read-only
and never written, and the guarded rows are materialised into a throwaway
in-memory database that `hard_filter` and `rank` see instead.

Two numbers are reported side by side, because they fail for different reasons
and have different fixes:

  coverage  — was the true acquirer in the index at all? If not, no ranker could
              have surfaced it. That is an index-building failure.
  recall@k  — given that it was in the index, did it make the top k? That is a
              ranking failure.

Both are meaningless without the universe size, so the number of funds is
printed next to every recall figure. Recall@20 out of 62 funds is a weak claim;
recall@20 out of 2,000 is a strong one.

    PYTHONPATH=. .venv/bin/python -m buyerlist.evalset.run_eval --dry-run
    PYTHONPATH=. .venv/bin/python -m buyerlist.evalset.run_eval --limit 10
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table

from ..index.store import DB_PATH, SCHEMA, load_funds, upsert_fund
from ..schemas import FundProfile, RankedMatches

console = Console()

DEALS_PATH = Path("buyerlist/evalset/deals.jsonl")
OUT_PATH = Path("out/backtest.json")
RECALL_KS = (5, 10, 20)

# Entity suffixes that carry no identifying information. Deliberately limited to
# legal-form words: stripping "Partners", "Capital" or "Group" as well would
# collapse Riverside Partners and Riverside Capital into the same firm, and a
# false positive here silently inflates every recall number in the report.
LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "lllp", "llp", "lp", "ltd", "limited",
    "co", "corp", "corporation", "company", "plc", "pllc", "pc", "pa",
    "gmbh", "ag", "sa", "nv", "bv", "sarl", "oy", "ab", "as", "pty",
}

# Name-level noise that survives punctuation stripping.
_APOSTROPHES = "'’ʼ`"


def normalize_name(raw: str | None) -> str:
    """Casefold, de-punctuate and strip legal suffixes to a comparable core.

    "Hoffmann Brothers Heating & Air Conditioning, Inc." -> "hoffmann brothers
    heating and air conditioning". Apostrophes are removed rather than spaced,
    so "Bob's Plumbing" and "Bobs Plumbing" agree.
    """
    if not raw:
        return ""
    s = raw.casefold()
    s = s.translate({ord(c): "" for c in _APOSTROPHES})
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = s.split()

    # A leading article is never identifying: "The Riverside Company".
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    # Suffixes can stack ("Foo Holdings Co. LLC"), so peel until stable.
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def names_match(a: str | None, b: str | None, *, fuzz: float = 0.90) -> bool:
    """Do two names refer to the same entity?

    Three tiers, ordered by how much they can be trusted:
      1. identical normalized forms;
      2. one normalized token set contained in the other, when the shorter side
         has at least two tokens — this catches "Hoffmann Brothers" vs
         "Hoffmann Brothers Heating and Air". The two-token floor is what stops
         a single generic word ("Riverside") from matching everything;
      3. a high character-level similarity, for spelling and spacing drift.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True

    ta, tb = set(na.split()), set(nb.split())
    smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(smaller) >= 2 and smaller <= larger:
        return True

    return SequenceMatcher(None, na, nb).ratio() >= fuzz


# --------------------------------------------------------------------------
# Deals
# --------------------------------------------------------------------------


@dataclass
class Deal:
    """One historical acquisition. Extra keys in the JSONL are tolerated."""

    acquirer_fund: str
    target: str
    target_url: str
    announced: str | None = None      # YYYY-MM
    platform: str | None = None       # the acquirer's platform that bought it
    sector: str | None = None
    state: str | None = None
    source_url: str | None = None
    notes: str | None = None

    @property
    def announced_month(self) -> str | None:
        """Normalized to YYYY-MM, or None if the source gave us nothing usable."""
        if not self.announced:
            return None
        m = re.match(r"(\d{4})[-/]?(\d{2})?", str(self.announced).strip())
        if not m:
            return None
        return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1) + "-12"

    @property
    def announced_year(self) -> int | None:
        m = re.match(r"(\d{4})", str(self.announced or "").strip())
        return int(m.group(1)) if m else None


def load_deals(path: Path) -> list[Deal]:
    """Read deals.jsonl. Missing file and bad lines are survivable, not fatal.

    The eval set is written by a separate process, so this runs against a file
    that may not exist yet or may be half-written.
    """
    if not path.exists():
        console.print(f"[yellow]no eval set at {path} — nothing to score yet[/yellow]")
        return []

    deals: list[Deal] = []
    known = {f.name for f in Deal.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
            deals.append(Deal(**{k: v for k, v in raw.items() if k in known}))
        except Exception as exc:  # a truncated final line is expected mid-write
            console.print(f"[yellow]skipping {path.name}:{lineno} — {exc}[/yellow]")
    return deals


# --------------------------------------------------------------------------
# The leakage guard
# --------------------------------------------------------------------------


@dataclass
class GuardStats:
    """What the guard deleted, per deal. Reported so the removal is auditable."""

    platforms_named_target: list[str] = field(default_factory=list)
    platforms_after_deal: list[str] = field(default_factory=list)
    addons_named_target: list[str] = field(default_factory=list)
    addons_after_deal: int = 0
    funds_touched: int = 0

    @property
    def total_removed(self) -> int:
        return (
            len(self.platforms_named_target)
            + len(self.platforms_after_deal)
            + len(self.addons_named_target)
            + self.addons_after_deal
        )


def guard_index(rows: list[dict], deal: Deal) -> tuple[list[dict], GuardStats]:
    """Return deep copies of `rows` with post-deal knowledge removed.

    Pure: the input rows (and the database behind them) are never mutated.

    Four removals, all applied across the *whole* index rather than just the
    acquirer — a target that shows up on some other firm's portfolio page is
    just as much of a giveaway:

      (a) platforms whose name matches the target;
      (b) add-ons whose name matches the target;
      (c) add-ons announced after the deal month;
      (d) platforms acquired in a year after the deal year.

    Note what is deliberately *kept*: `deal.platform`, the acquirer's roll-up
    that made the purchase. It existed before the deal, and the whole thesis
    under test is "this fund owns a platform rolling up exactly this business".
    Removing it would guard away the signal instead of the answer.
    """
    stats = GuardStats()
    cutoff_month = deal.announced_month
    cutoff_year = deal.announced_year
    guarded: list[dict] = []

    for row in rows:
        profile: FundProfile = row["profile"].model_copy(deep=True)
        touched = False
        kept_platforms = []

        for plat in profile.platforms:
            if names_match(plat.name, deal.target):
                stats.platforms_named_target.append(f"{profile.firm_name}: {plat.name}")
                touched = True
                continue
            # Year granularity is all the index has. Strictly-after keeps
            # same-year history that genuinely predates the deal.
            if (
                cutoff_year is not None
                and plat.acquired_year is not None
                and plat.acquired_year > cutoff_year
            ):
                stats.platforms_after_deal.append(
                    f"{profile.firm_name}: {plat.name} ({plat.acquired_year})"
                )
                touched = True
                continue

            kept_addons = []
            for addon in plat.add_ons:
                if names_match(addon.name, deal.target):
                    stats.addons_named_target.append(
                        f"{profile.firm_name}/{plat.name}: {addon.name}"
                    )
                    touched = True
                    continue
                if (
                    cutoff_month is not None
                    and addon.announced
                    and _month_key(addon.announced) > cutoff_month
                ):
                    stats.addons_after_deal += 1
                    touched = True
                    continue
                kept_addons.append(addon)

            plat.add_ons = kept_addons
            kept_platforms.append(plat)

        profile.platforms = kept_platforms
        if touched:
            stats.funds_touched += 1
        guarded.append({**row, "profile": profile})

    return guarded, stats


def _month_key(raw: str) -> str:
    """Coerce an add-on date to a YYYY-MM string that sorts correctly.

    Anything unparseable sorts as "" so it is never treated as post-deal — an
    unknown date is not evidence of leakage.
    """
    m = re.match(r"(\d{4})[-/]?(\d{2})?", str(raw).strip())
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)}" if m.group(2) else f"{m.group(1)}-01"


# --------------------------------------------------------------------------
# Index plumbing
# --------------------------------------------------------------------------


def open_index_readonly(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the fund index read-only.

    `store.connect` would run the schema script, which takes a write lock on a
    file another process may be building. Read-only mode makes that impossible
    by construction.
    """
    if not path.exists():
        raise FileNotFoundError(f"fund index not found at {path} — run `index build` first")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def materialize(rows: list[dict]) -> sqlite3.Connection:
    """Write guarded rows into a throwaway in-memory index.

    `hard_filter` and `rank` both re-read the index from a connection, so the
    only way to hand them a guarded view — without touching their code or the
    real database — is to give them a different connection.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for row in rows:
        upsert_fund(
            conn,
            domain=row["domain"],
            tier=row.get("tier") or "shallow",
            discovered_via=row.get("discovered_via"),
            profile=row["profile"],
            scraped_at=row.get("scraped_at") or "",
            pages_fetched=row.get("pages_fetched") or 0,
        )
    return conn


def find_indexed_firm(rows: list[dict], firm_name: str) -> str | None:
    """The index's own spelling of `firm_name`, or None if it is absent.

    Coverage is measured here and nowhere else: if this returns None the tool
    could not have produced the right answer at any k.
    """
    for row in rows:
        indexed = row["profile"].firm_name
        if names_match(indexed, firm_name):
            return indexed
    return None


def split_hallucinations(
    matches: Iterable[Any], candidates: list[dict]
) -> tuple[list, list, list]:
    """(kept, out_of_index_firms, out_of_index_platforms).

    Mirrors the runtime guard in `cli.cmd_run`. A match whose `via_platform` is
    not a platform we supplied is a fabricated add-on thesis — the heaviest
    scoring dimension resting on a company that does not exist — so it is
    dropped rather than downgraded, exactly as an out-of-index firm name is.
    Comparison is on `normalize_name` so punctuation and casing drift is not
    mistaken for a hallucination.
    """
    allowed_firms = {normalize_name(c["profile"].firm_name) for c in candidates}
    allowed_platforms = {
        normalize_name(p.name) for c in candidates for p in c["profile"].platforms
    }
    kept, bad_firms, bad_platforms = [], [], []
    for m in matches:
        if normalize_name(m.firm_name) not in allowed_firms:
            bad_firms.append(m)
        elif (m.via_platform
              and normalize_name(m.via_platform) not in allowed_platforms):
            bad_platforms.append(m)
        else:
            kept.append(m)
    return kept, bad_firms, bad_platforms


def rank_of(matches: Iterable[Any], acquirer: str) -> int | None:
    """1-based position of the true acquirer in the ranked list, else None."""
    for i, m in enumerate(matches, 1):
        if names_match(m.firm_name, acquirer):
            return i
    return None


# --------------------------------------------------------------------------
# Scoring one deal
# --------------------------------------------------------------------------


def score_deal(
    deal: Deal,
    base_rows: list[dict],
    *,
    llm,
    max_pages: int,
    dry_run: bool,
) -> dict:
    """Run the full pipeline for one deal against a guarded index.

    Returns a record even on failure: a dead target site is a data problem, not
    a reason to lose the other 40 deals in the run.
    """
    guarded_rows, guard = guard_index(base_rows, deal)
    indexed_as = find_indexed_firm(base_rows, deal.acquirer_fund)

    rec: dict[str, Any] = {
        "target": deal.target,
        "target_url": deal.target_url,
        "acquirer_fund": deal.acquirer_fund,
        "via_platform": deal.platform,
        "announced": deal.announced,
        "sector": deal.sector,
        "state": deal.state,
        "universe": len(base_rows),
        "acquirer_in_index": indexed_as is not None,
        "acquirer_indexed_as": indexed_as,
        "guard": {
            "funds_touched": guard.funds_touched,
            "total_removed": guard.total_removed,
            "platforms_named_target": guard.platforms_named_target,
            "platforms_after_deal": guard.platforms_after_deal[:10],
            "platforms_after_deal_count": len(guard.platforms_after_deal),
            "addons_named_target": guard.addons_named_target,
            "addons_after_deal": guard.addons_after_deal,
        },
        "survived_filter": None,
        "candidates": None,
        "rank": None,
        "top_5": [],
        "error": None,
    }

    if dry_run:
        # Everything above is pure Python. Stop before the first billable call.
        return rec

    # Imported lazily so --dry-run works with no API key and no network stack.
    from ..extract import estimate_size, extract_company
    from ..fetch import fetch_site_sync
    from ..match import hard_filter, rank

    conn = None
    try:
        snap = fetch_site_sync(deal.target_url, "company", max_pages=max_pages)
        if not snap.pages:
            raise RuntimeError(f"site unreadable: {'; '.join(snap.notes) or 'no pages'}")

        profile = extract_company(llm, snap)
        size = estimate_size(llm, profile)
        rec["extracted_name"] = profile.legal_name
        rec["extracted_naics"] = [n.code for n in profile.naics_codes]
        rec["ebitda_mid_usd"] = (size.ebitda_low_usd + size.ebitda_high_usd) // 2
        rec["size_confidence"] = size.confidence

        conn = materialize(guarded_rows)
        candidates, diag = hard_filter(conn, profile, size)
        rec["candidates"] = diag["kept"]
        rec["filter_drops"] = diag["dropped"]
        # Distinguishes a filter failure from a ranking failure: if the acquirer
        # never reached the model, the rubric is not what needs fixing.
        rec["survived_filter"] = bool(
            indexed_as and any(names_match(c["profile"].firm_name, deal.acquirer_fund)
                               for c in candidates)
        )
        if not candidates:
            raise RuntimeError("no candidates survived the hard filter")

        ranked: RankedMatches = rank(llm, conn, profile, size, candidates)
        # Same guardrail as `cli.cmd_run`: nothing the model invented may score.
        matches, bad_firms, bad_platforms = split_hallucinations(
            ranked.matches, candidates
        )
        rec["hallucinated"] = [m.firm_name for m in bad_firms]
        rec["hallucinated_platforms"] = [
            f"{m.firm_name} via {m.via_platform}" for m in bad_platforms
        ]
        rec["rank"] = rank_of(matches, deal.acquirer_fund)
        rec["top_5"] = [m.firm_name for m in matches[:5]]
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc(limit=4)
    finally:
        if conn is not None:
            conn.close()

    return rec


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def compute_metrics(records: list[dict], universe: int, *, dry_run: bool = False) -> dict:
    """recall@k both unconditionally and conditioned on coverage.

    Deals that errored are excluded from the recall denominators and counted
    separately — folding a dead website into a ranking metric measures the
    crawler, not the ranker. In a dry run nothing was ranked at all, so the
    rank-derived figures are reported as null rather than as a very convincing
    0% — an unmeasured metric must never read as a measured failure.
    """
    scored = [r for r in records if r["error"] is None]
    errored = [r for r in records if r["error"] is not None]
    in_index = [r for r in scored if r["acquirer_in_index"]]
    found = [r for r in scored if r["rank"] is not None]

    def recall(rows: list[dict], k: int) -> float | None:
        if dry_run or not rows:
            return None
        hits = sum(1 for r in rows if r["rank"] is not None and r["rank"] <= k)
        return hits / len(rows)

    return {
        "universe_funds": universe,
        "deals_total": len(records),
        "deals_scored": len(scored),
        "deals_errored": len(errored),
        "coverage_n": sum(1 for r in records if r["acquirer_in_index"]),
        "coverage_pct": (
            sum(1 for r in records if r["acquirer_in_index"]) / len(records)
            if records else None
        ),
        "recall_all": {f"@{k}": recall(scored, k) for k in RECALL_KS},
        "recall_all_n": len(scored),
        "recall_in_index": {f"@{k}": recall(in_index, k) for k in RECALL_KS},
        "recall_in_index_n": len(in_index),
        "median_rank": statistics.median(r["rank"] for r in found) if found else None,
        "mean_rank": (sum(r["rank"] for r in found) / len(found)) if found else None,
        "found_n": None if dry_run else len(found),
        "survived_filter_n": None if dry_run else sum(1 for r in scored if r["survived_filter"]),
        "guard_removals_total": sum(r["guard"]["total_removed"] for r in records),
        "guard_direct_leaks": sum(
            len(r["guard"]["platforms_named_target"]) + len(r["guard"]["addons_named_target"])
            for r in records
        ),
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def print_report(records: list[dict], m: dict, dry_run: bool, cost: float) -> None:
    universe = m["universe_funds"]

    console.rule("[bold]backtest")
    console.print(
        f"{m['deals_total']} deals · index universe {universe} funds"
        + (" · [yellow]DRY RUN (no model calls)[/yellow]" if dry_run else "")
    )

    cov = Table(show_header=False, box=None)
    cov.add_row(
        "coverage",
        f"{m['coverage_n']}/{m['deals_total']} ({_pct(m['coverage_pct'])}) "
        f"acquirers present in the {universe}-fund index",
    )
    cov.add_row(
        "leakage guard",
        f"{m['guard_removals_total']} index entries removed across these deals, "
        f"of which {m['guard_direct_leaks']} name the target itself",
    )
    if not dry_run:
        cov.add_row("scored", f"{m['deals_scored']} ({m['deals_errored']} errored)")
        cov.add_row(
            "survived filter",
            f"{m['survived_filter_n']}/{m['recall_in_index_n']} of indexed acquirers "
            "reached the ranker",
        )
    console.print(cov)

    if dry_run:
        _print_guard_table(records)
        return

    t = Table(show_header=True, header_style="bold", title="recall (universe: "
                                                           f"{universe} funds)")
    for col in ("basis", "n", "@5", "@10", "@20"):
        t.add_column(col)
    t.add_row(
        "all deals", str(m["recall_all_n"]),
        *[_pct(m["recall_all"][f"@{k}"]) for k in RECALL_KS],
    )
    t.add_row(
        "acquirer in index", str(m["recall_in_index_n"]),
        *[_pct(m["recall_in_index"][f"@{k}"]) for k in RECALL_KS],
    )
    console.print(t)
    console.print(
        f"median rank of true acquirer: "
        f"{m['median_rank'] if m['median_rank'] is not None else '—'} "
        f"(over {m['found_n']} deals where it was ranked at all, out of {universe} funds)"
    )

    detail = Table(show_header=True, header_style="bold", title="per-deal")
    for col in ("target", "true acquirer", "in idx", "cand", "rank", "guard", "note"):
        detail.add_column(col)
    for r in records:
        detail.add_row(
            (r["target"] or "?")[:26],
            (r["acquirer_fund"] or "?")[:24],
            "yes" if r["acquirer_in_index"] else "[red]NO[/red]",
            str(r["candidates"] if r["candidates"] is not None else "—"),
            ("[green]" + str(r["rank"]) + "[/green]") if r["rank"] else "—",
            str(r["guard"]["total_removed"]),
            ("[red]" + r["error"][:38] + "[/red]") if r["error"] else "",
        )
    console.print(detail)
    console.print(f"\n[bold]${cost:.4f}[/bold] model spend")


def _print_guard_table(records: list[dict]) -> None:
    """Dry-run view: prove the guard fires, and show what it deleted."""
    t = Table(show_header=True, header_style="bold",
              title="leakage guard (dry run — pure Python, no API)")
    for col in ("target", "month", "true acquirer", "in idx",
                "plat=tgt", "addon=tgt", "late plat", "late addon"):
        t.add_column(col)
    for r in records:
        g = r["guard"]
        t.add_row(
            (r["target"] or "?")[:24],
            r["announced"] or "?",
            (r["acquirer_fund"] or "?")[:24],
            "yes" if r["acquirer_in_index"] else "[red]NO[/red]",
            str(len(g["platforms_named_target"])),
            str(len(g["addons_named_target"])),
            str(g["platforms_after_deal_count"]),
            str(g["addons_after_deal"]),
        )
    console.print(t)

    leaks = [
        name
        for r in records
        for name in r["guard"]["platforms_named_target"] + r["guard"]["addons_named_target"]
    ]
    if leaks:
        console.print("\n[bold]removed direct leaks:[/bold]")
        for name in leaks:
            console.print(f"  - {name}")
    else:
        console.print(
            "\n[yellow]no direct target leaks found — either the index predates these "
            "deals or the targets are not named on the portfolio pages[/yellow]"
        )


# --------------------------------------------------------------------------
# Self-tests (no API, no database)
# --------------------------------------------------------------------------


def _self_test() -> int:
    """Prove the normalization and the guard behave, cheaply and offline."""
    from ..schemas import AddOn, Platform

    # -- normalization ----------------------------------------------------
    assert normalize_name("Hoffmann Brothers Heating & Air Conditioning, Inc.") == \
        "hoffmann brothers heating and air conditioning"
    assert normalize_name("The Riverside Company") == "riverside"
    assert normalize_name("ACME Holdings Co. LLC") == "acme holdings"
    assert normalize_name("Bob's Plumbing") == normalize_name("Bobs Plumbing")
    assert normalize_name(None) == "" and normalize_name("") == ""

    assert names_match("Apex Service Partners, LLC", "apex service partners")
    assert names_match("Hoffmann Brothers", "Hoffmann Brothers Heating and Air")
    assert names_match("Alpine Investors", "Alpine Investors LP")
    # Generic single tokens must not collapse distinct firms.
    assert not names_match("Riverside Partners", "The Riverside Company")
    assert not names_match("Acme Plumbing", "Zenith Plumbing")
    assert not names_match("", "Anything")

    # -- month coercion ---------------------------------------------------
    assert _month_key("2023-07") == "2023-07"
    assert _month_key("2023") == "2023-01"
    assert _month_key("unknown") == ""

    # -- the guard --------------------------------------------------------
    def _profile() -> FundProfile:
        return FundProfile(
            firm_name="Test Capital Partners", website="https://test.example",
            hq_city="Dallas", hq_state="TX", other_offices=[], control="control",
            deal_types=["platform"], ebitda_min_usd=None, ebitda_max_usd=None,
            ev_min_usd=None, ev_max_usd=None, revenue_min_usd=None, revenue_max_usd=None,
            naics_coverage=["238"], sector_themes=["services"], geographies=["US"],
            exclusions=[], latest_fund=None, latest_fund_size_usd=None,
            evidence=[], extraction_confidence="high",
            platforms=[
                Platform(
                    name="Apex Service Partners", sector="HVAC", naics_codes=["238220"],
                    description="roll-up", hq_state="FL", acquired_year=2019,
                    role="platform", parent_platform=None,
                    add_ons=[
                        AddOn(name="Early Air Co", announced="2021-03", city="Tampa", state="FL"),
                        AddOn(name="Hoffmann Brothers, Inc.", announced="2023-07",
                              city="St. Louis", state="MO"),
                        AddOn(name="Later Air Co", announced="2024-02", city="Miami", state="FL"),
                        AddOn(name="Undated Co", announced=None, city=None, state=None),
                    ],
                ),
                Platform(
                    name="Hoffmann Brothers Heating & Air Conditioning, Inc.",
                    sector="HVAC", naics_codes=["238220"], description="the target itself",
                    hq_state="MO", acquired_year=2023, role="add_on",
                    parent_platform="Apex Service Partners", add_ons=[],
                ),
                Platform(
                    name="Bought Later Co", sector="HVAC", naics_codes=["238220"],
                    description="post-deal platform", hq_state="TX", acquired_year=2025,
                    role="platform", parent_platform=None, add_ons=[],
                ),
            ],
        )

    rows = [{
        "domain": "test.example", "firm_name": "Test Capital Partners", "tier": "deep",
        "discovered_via": "seed", "scraped_at": "2026-01-01", "pages_fetched": 9,
        "profile": _profile(),
    }]
    deal = Deal(
        acquirer_fund="Test Capital Partners, LLC",
        target="Hoffmann Brothers Heating and Air Conditioning",
        target_url="https://hoffmannbrothers.com", announced="2023-07",
        platform="Apex Service Partners",
    )

    guarded, stats = guard_index(rows, deal)
    plats = {p.name for p in guarded[0]["profile"].platforms}

    # (a) the target as a platform is gone; (d) so is the 2025 acquisition.
    assert "Hoffmann Brothers Heating & Air Conditioning, Inc." not in plats, plats
    assert "Bought Later Co" not in plats, plats
    # The acquirer's own platform survives: it predates the deal and is the thesis.
    assert "Apex Service Partners" in plats, plats

    addons = {a.name for p in guarded[0]["profile"].platforms for a in p.add_ons}
    assert "Hoffmann Brothers, Inc." not in addons, addons   # (b) target as add-on
    assert "Later Air Co" not in addons, addons              # (c) announced after
    assert "Early Air Co" in addons, addons                  # pre-deal history kept
    assert "Undated Co" in addons, addons                    # unknown date != leakage
    # Same-month add-ons are kept: "after" is strictly after.
    assert stats.funds_touched == 1
    assert len(stats.platforms_named_target) == 1
    assert len(stats.platforms_after_deal) == 1
    assert len(stats.addons_named_target) == 1
    assert stats.addons_after_deal == 1
    assert stats.total_removed == 4

    # Purity: the caller's rows are untouched.
    assert len(rows[0]["profile"].platforms) == 3
    assert len(rows[0]["profile"].platforms[0].add_ons) == 4

    # A deal with no date must not silently drop dated history.
    _, undated = guard_index(rows, Deal(acquirer_fund="X", target="Nothing Ltd",
                                        target_url="https://x.example", announced=None))
    assert undated.total_removed == 0

    # Coverage matching goes through the same normalization.
    assert find_indexed_firm(rows, "Test Capital Partners LLC") == "Test Capital Partners"
    assert find_indexed_firm(rows, "Some Other Fund") is None

    # -- hallucination guard ----------------------------------------------
    from ..schemas import Match

    def _match(firm: str, platform: str | None) -> Match:
        return Match(firm_name=firm, via_platform=platform, dimension_scores=[],
                     score=50, tier="tier_2", rationale="r", angle="a",
                     evidence=[], confidence="medium")

    real, invented_firm, invented_plat, direct, drifted = (
        _match("Test Capital Partners", "Apex Service Partners"),
        _match("Nonexistent Capital", None),
        _match("Test Capital Partners", "Imaginary Roll-Up Co"),
        _match("Test Capital Partners", None),
        _match("test capital partners, llc", "apex service partners"),
    )
    kept, bad_firms, bad_plats = split_hallucinations(
        [real, invented_firm, invented_plat, direct, drifted], rows
    )
    assert kept == [real, direct, drifted], kept   # formatting drift is not a lie
    assert bad_firms == [invented_firm]
    assert bad_plats == [invented_plat]            # fabricated thesis, dropped

    console.print("[green]self-tests passed[/green]")
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="buyerlist.evalset.run_eval",
        description="Backtest recall@k against historical acquisitions, with a leakage guard.",
    )
    ap.add_argument("--deals", type=Path, default=DEALS_PATH)
    ap.add_argument("--db", type=Path, default=DB_PATH, help="fund index (opened read-only)")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--limit", type=int, default=None, help="score only the first N deals")
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument(
        "--dry-run", action="store_true",
        help="leakage guard + coverage only; makes no model calls and needs no API key",
    )
    ap.add_argument("--self-test", action="store_true", help="run offline guard self-tests")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    deals = load_deals(args.deals)
    if not deals:
        return 1
    if args.limit is not None:
        deals = deals[: args.limit]

    try:
        conn = open_index_readonly(args.db)
        base_rows = load_funds(conn)
        conn.close()
    except Exception as exc:
        console.print(f"[red]cannot read fund index:[/red] {exc}")
        return 1
    if not base_rows:
        console.print("[red]fund index is empty — run `index build` first[/red]")
        return 1

    # Telemetry is shared across every deal so the report carries one honest
    # total for the whole backtest.
    llm = None
    tel = None
    if not args.dry_run:
        from ..llm import LLM, Telemetry
        tel = Telemetry()
        try:
            llm = LLM(tel)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return 1

    records: list[dict] = []
    for i, deal in enumerate(deals, 1):
        label = f"[{i}/{len(deals)}] {deal.target}"
        if args.dry_run:
            records.append(score_deal(deal, base_rows, llm=None,
                                      max_pages=args.max_pages, dry_run=True))
            continue
        with console.status(f"{label} …"):
            rec = score_deal(deal, base_rows, llm=llm,
                             max_pages=args.max_pages, dry_run=False)
        records.append(rec)
        if rec["error"]:
            console.print(f"{label}  [red]{rec['error'][:80]}[/red]")
        else:
            console.print(f"{label}  rank={rec['rank'] or '—'} "
                          f"cand={rec['candidates']} guard-removed={rec['guard']['total_removed']}")

    metrics = compute_metrics(records, len(base_rows), dry_run=args.dry_run)
    cost = tel.total_cost_usd if tel else 0.0

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": args.dry_run,
        "deals_path": str(args.deals),
        "db_path": str(args.db),
        "metrics": metrics,
        "deals": records,
        "cost_usd": cost,
        "telemetry": [asdict(s) | {"cost_usd": s.cost_usd} for s in tel.stages] if tel else [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print_report(records, metrics, args.dry_run, cost)
    console.print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
