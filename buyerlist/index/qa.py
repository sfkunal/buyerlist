"""Quality assurance for the scraped fund index.

The index is built by a cheap model (claude-haiku-4-5) at ~$0.02 a fund, which
invites the obvious objection: how do you know the cheap extraction is right?
This module is the answer, and it is the third of the three filters promised in
`seeds.py` — the first two only prove a firm *exists*, not that what we recorded
about it is true.

Two tools, because the two failure modes are different:

  `qa sample`  Draws a reproducible stratified sample and writes a worksheet a
               human can check against the firm's own pages. This is the only
               way to measure *accuracy* — no automated rule knows whether a
               firm really has a $3M EBITDA floor.

  `qa check`   Scans the whole index for entries that are internally
               implausible. It needs no human, no API and no network, so it can
               run on every build. It cannot prove an entry is right, but it
               reliably catches the ways a language model gets this wrong:
               transposed ranges, unit errors ($5 written as 5), malformed
               NAICS codes, and — the expensive one — a portfolio company filed
               under the firm's generic sector label instead of its actual
               trade. That last bug once mapped an HVAC roll-up to janitorial
               codes, which silently made it unmatchable.

Read-only by construction: the build process may be writing to the database
while this runs, so we open a `mode=ro` URI connection rather than
`store.connect()` (which would run CREATE TABLE IF NOT EXISTS — a write).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..fetch import CACHE_DIR
from ..schemas import FundProfile, Platform
from .store import DB_PATH, load_funds

console = Console()

OUT_DIR = Path("out")
WORKSHEET_PATH = OUT_DIR / "index_qa.md"
ISSUES_PATH = OUT_DIR / "index_qa_issues.json"

# Plausibility envelopes for the lower middle market. These are deliberately
# wide: the point is to catch a decimal/units error (the model writing 5 for
# "$5 million"), not to second-guess a firm's stated criteria. Anything inside
# the band is a question for the human sample, not for this file.
EBITDA_FLOOR_USD = 100_000
EBITDA_CEIL_USD = 500_000_000
EV_FLOOR_USD = 500_000
EV_CEIL_USD = 5_000_000_000

NAICS_RE = re.compile(r"^\d{2,6}$")

# 56xxxx is "Administrative and Support and Waste Management" — janitorial,
# landscaping, staffing. It is the code a model reaches for when it reads a
# portfolio table's generic "Services" column instead of the specific vertical
# next to it. Harmless on its own; a bug when the company's own text names a
# licensed trade that has its own code.
GENERIC_NAICS_PREFIX = "56"
TRADE_TERMS: dict[str, str] = {
    r"\bhvac\b": "hvac",
    r"\bplumb": "plumbing",
    r"\belectrical\b": "electrical",
    r"\broofing\b": "roofing",
    r"\bdental\b": "dental",
    r"\bveterinar": "veterinary",
}
_TRADE_RE = [(re.compile(pat), label) for pat, label in TRADE_TERMS.items()]

# code -> (severity, what it means). Drives report ordering and the JSON legend.
CHECKS: dict[str, tuple[str, str]] = {
    "ebitda_range_order": ("error", "ebitda_min_usd > ebitda_max_usd"),
    "ev_range_order": ("error", "ev_min_usd > ev_max_usd"),
    "revenue_range_order": ("error", "revenue_min_usd > revenue_max_usd"),
    "ebitda_magnitude": ("error", f"EBITDA outside ${EBITDA_FLOOR_USD:,}–${EBITDA_CEIL_USD:,}"),
    "ev_magnitude": ("error", f"EV outside ${EV_FLOOR_USD:,}–${EV_CEIL_USD:,}"),
    "naics_format": ("error", "NAICS code is not a 2–6 digit numeric string"),
    "generic_sector_mapping": ("error", "trade-specific company mapped only to 56xxxx codes"),
    "empty_extraction": ("error", "no platforms and no sector_themes — failed extraction"),
    "platform_no_naics": ("warn", "portfolio company has no NAICS codes"),
    "addon_no_parent": ("warn", "role=add_on but parent_platform is null"),
    "duplicate_platform": ("warn", "same platform name listed twice in one fund"),
}


@dataclass
class Issue:
    """One flagged entry. `subject` names the platform when the fund is fine."""

    domain: str
    firm_name: str
    code: str
    severity: str
    subject: str | None
    detail: str


# --------------------------------------------------------------------------
# Read-only access
# --------------------------------------------------------------------------


def open_ro(path: Path = DB_PATH) -> sqlite3.Connection | None:
    """Open the index read-only. Returns None (after printing why) if unusable.

    A build may be running against this file, so we never create it and never
    write to it — including the implicit schema write `store.connect()` does.
    """
    if not path.exists():
        console.print(f"[red]No index at {path}.[/red] Run `index build` first.")
        return None
    try:
        # timeout covers the moments the builder holds the write lock.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    except sqlite3.OperationalError as e:
        console.print(f"[red]Could not open {path} read-only:[/red] {e}")
        return None
    conn.row_factory = sqlite3.Row
    return conn


def load_index(path: Path = DB_PATH) -> list[dict] | None:
    conn = open_ro(path)
    if conn is None:
        return None
    try:
        rows = load_funds(conn)
    except sqlite3.OperationalError as e:
        # Most likely "no such table: funds" on a database created but not yet
        # populated by the build.
        console.print(f"[red]Index unreadable:[/red] {e}")
        return None
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]Index is empty — nothing to QA (build in progress?).[/yellow]")
    return rows


# --------------------------------------------------------------------------
# Automated consistency checks
# --------------------------------------------------------------------------


def _usd(n: int | None) -> str:
    return "null" if n is None else f"${n:,}"


def _check_ranges(p: FundProfile, add) -> None:
    pairs = [
        ("ebitda", p.ebitda_min_usd, p.ebitda_max_usd, EBITDA_FLOOR_USD, EBITDA_CEIL_USD),
        ("ev", p.ev_min_usd, p.ev_max_usd, EV_FLOOR_USD, EV_CEIL_USD),
        # Revenue has no published envelope worth asserting (a $200M-revenue
        # target can still be a lower-middle-market deal), so only ordering.
        ("revenue", p.revenue_min_usd, p.revenue_max_usd, None, None),
    ]
    for name, lo, hi, floor, ceil in pairs:
        if lo is not None and hi is not None and lo > hi:
            add(f"{name}_range_order", None, f"{name}_min={_usd(lo)} > {name}_max={_usd(hi)}")
        if floor is None:
            continue
        for field, v in ((f"{name}_min_usd", lo), (f"{name}_max_usd", hi)):
            if v is None or v == 0:
                continue
            if v < floor or v > ceil:
                add(f"{name}_magnitude", None, f"{field}={_usd(v)} (likely a units error)")


def _check_naics(codes: list[str], where: str, subject: str | None, add) -> None:
    for c in codes:
        if not NAICS_RE.match(str(c).strip()):
            add("naics_format", subject, f"{where}: {c!r}")


def _trade_mentioned(pl: Platform) -> str | None:
    """The specific trade a platform's own text names, if any."""
    text = f"{pl.name} {pl.sector} {pl.description}".lower()
    for rx, label in _TRADE_RE:
        if rx.search(text):
            return label
    return None


def check_fund(row: dict) -> list[Issue]:
    """All consistency issues for one indexed fund."""
    p: FundProfile = row["profile"]
    issues: list[Issue] = []

    def add(code: str, subject: str | None, detail: str) -> None:
        issues.append(
            Issue(
                domain=row["domain"],
                firm_name=p.firm_name,
                code=code,
                severity=CHECKS[code][0],
                subject=subject,
                detail=detail,
            )
        )

    _check_ranges(p, add)
    _check_naics(p.naics_coverage, "naics_coverage", None, add)

    if not p.platforms and not p.sector_themes:
        add("empty_extraction", None, f"tier={row['tier']} pages={row['pages_fetched']}")

    seen: Counter[str] = Counter()
    for pl in p.platforms:
        seen[pl.name.strip().lower()] += 1
        _check_naics(pl.naics_codes, "platform naics_codes", pl.name, add)

        if not pl.naics_codes:
            add("platform_no_naics", pl.name, f"sector={pl.sector!r}")
        if pl.role == "add_on" and not pl.parent_platform:
            add("addon_no_parent", pl.name, "add-on with no named parent platform")

        # The known-bad pattern: every code is generic admin/support while the
        # company's own words name a licensed trade with its own NAICS code.
        codes = [str(c).strip() for c in pl.naics_codes if str(c).strip()]
        if codes and all(c.startswith(GENERIC_NAICS_PREFIX) for c in codes):
            trade = _trade_mentioned(pl)
            if trade:
                add(
                    "generic_sector_mapping",
                    pl.name,
                    f"text mentions {trade} but naics_codes={','.join(codes)} are all 56xxxx",
                )

    for name, n in seen.items():
        if n > 1:
            add("duplicate_platform", name, f"listed {n} times")

    return issues


def run_checks(rows: list[dict]) -> list[Issue]:
    return [i for row in rows for i in check_fund(row)]


def summarize(rows: list[dict], issues: list[Issue]) -> dict:
    by_code: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        by_code[i.code].append(i)

    summary = {}
    for code, (severity, desc) in CHECKS.items():
        hits = by_code.get(code, [])
        summary[code] = {
            "severity": severity,
            "description": desc,
            "count": len(hits),
            "funds_affected": len({h.domain for h in hits}),
            "examples": [f"{h.firm_name}: {h.subject + ' — ' if h.subject else ''}{h.detail}"
                         for h in hits[:3]],
        }

    n_platforms = sum(len(r["profile"].platforms) for r in rows)
    clean = len(rows) - len({i.domain for i in issues})
    return {
        "funds_checked": len(rows),
        "platforms_checked": n_platforms,
        "funds_with_no_issues": clean,
        "clean_rate": round(clean / len(rows), 3) if rows else 0.0,
        "errors": sum(1 for i in issues if i.severity == "error"),
        "warnings": sum(1 for i in issues if i.severity == "warn"),
        "checks": summary,
    }


# --------------------------------------------------------------------------
# Stratified sampling for human review
# --------------------------------------------------------------------------


def _stratum(row: dict) -> tuple[str, str]:
    """Portfolio depth x whether a size band was extracted.

    Those two axes are where extraction quality actually varies: a fund with 40
    platforms exercises the portfolio prompt hard, and a fund with a published
    EBITDA band exercises the numeric parsing that unit errors live in. Sampling
    only the rich entries would flatter the index; only the thin ones would
    libel it.
    """
    n = len(row["profile"].platforms)
    depth = "none" if n == 0 else "small" if n <= 5 else "mid" if n <= 20 else "large"
    band = "band" if row["profile"].ebitda_min_usd is not None else "no_band"
    return depth, band


def stratified_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    """Deterministic round-robin draw across strata, so re-running reproduces it."""
    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        strata[_stratum(row)].append(row)

    rng = random.Random(seed)
    for key in strata:
        # Sort first: SQLite row order is not a guarantee, and the seed should
        # be the only thing that decides the sample.
        strata[key].sort(key=lambda r: r["domain"])
        rng.shuffle(strata[key])

    picked: list[dict] = []
    keys = sorted(strata)
    while len(picked) < n and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(picked) < n:
                picked.append(strata[k].pop())
    return picked


def _cached_page_urls(domain: str, limit: int = 8) -> list[str]:
    """Page URLs the crawler actually fetched for this domain, from the disk cache.

    The cache is keyed by URL hash so it cannot be queried directly; each file
    begins with its own url, so a head-read is enough and costs no network.
    """
    if not CACHE_DIR.exists():
        return []
    host = domain.removeprefix("www.")
    found: set[str] = set()
    for p in sorted(CACHE_DIR.glob("*.json")):
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                head = fh.read(400)
        except OSError:
            continue
        m = re.search(r'"url":\s*"([^"]+)"', head)
        if m and host in m.group(1):
            found.add(m.group(1))
            if len(found) >= limit:
                break
    return sorted(found)


# --------------------------------------------------------------------------
# Worksheet rendering
# --------------------------------------------------------------------------

WORKSHEET_HEADER = """\
# Fund index — human verification worksheet

Generated by `buyerlist.index.qa sample`. Sample is stratified by portfolio
depth and whether an EBITDA band was extracted, and is reproducible from the
seed below.

**How to use:** open each firm's source pages, then for every row write `ok`,
`wrong`, or `n/a` in the *verdict* column and put the true value in *correction*.
`n/a` means the site does not state it and the index correctly left it empty —
that is a pass, not a miss. Tally at the bottom.

A field is only wrong if the firm's own site says something different. Absent
data recorded as null/empty is correct behaviour.
"""

FIELD_ROWS: list[tuple[str, str]] = [
    ("ebitda_min_usd", "EBITDA floor"),
    ("ebitda_max_usd", "EBITDA ceiling"),
    ("ev_min_usd", "EV floor"),
    ("ev_max_usd", "EV ceiling"),
    ("control", "control / minority"),
    ("geographies", "geographies"),
    ("naics_coverage", "NAICS coverage"),
    ("sector_themes", "sector themes"),
    ("exclusions", "exclusions"),
]


def _fmt(v) -> str:
    if v is None:
        return "_(null)_"
    if isinstance(v, list):
        if not v:
            return "_(empty)_"
        return ", ".join(str(x) for x in v[:12]) + (" …" if len(v) > 12 else "")
    if isinstance(v, int):
        return f"${v:,}"
    return str(v)


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_worksheet(sample: list[dict], seed: int, n_index: int, with_pages: bool) -> str:
    lines = [WORKSHEET_HEADER, f"\nsample: **{len(sample)}** of {n_index} indexed funds · "
             f"seed `{seed}`\n"]

    for i, row in enumerate(sample, 1):
        p: FundProfile = row["profile"]
        depth, band = _stratum(row)
        lines.append(f"\n---\n\n## {i}. {p.firm_name}")
        lines.append(
            f"\n- source: <{p.website}>\n"
            f"- domain: `{row['domain']}` · tier: {row['tier']} · stratum: {depth}/{band}\n"
            f"- scraped: {row['scraped_at']} · pages fetched: {row['pages_fetched']} · "
            f"model's own confidence: {p.extraction_confidence}"
        )

        # Pages the checker can open: what the model cited, then what was fetched.
        cited = sorted({e.source_url for e in p.evidence if e.source_url})
        if cited:
            lines.append("\n**Pages cited by the extraction:**\n")
            lines += [f"- <{u}>" for u in cited[:10]]
        if with_pages:
            extra = [u for u in _cached_page_urls(row["domain"]) if u not in set(cited)]
            if extra:
                lines.append("\n**Other pages fetched:**\n")
                lines += [f"- <{u}>" for u in extra]

        lines.append("\n| field | extracted | verdict (ok/wrong/n/a) | correction |")
        lines.append("| --- | --- | --- | --- |")
        for attr, label in FIELD_ROWS:
            val = _md_cell(_fmt(getattr(p, attr)))
            lines.append(f"| {label} (`{attr}`) | {val} |  |  |")

        lines.append(f"\n**Portfolio** — first 10 of {len(p.platforms)}:\n")
        if not p.platforms:
            lines.append("_no portfolio companies extracted_")
        else:
            lines.append("| # | company | sector | role | parent | naics_codes | "
                         "verdict | correct naics |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            for j, pl in enumerate(p.platforms[:10], 1):
                lines.append(
                    f"| {j} | {_md_cell(pl.name)} | {_md_cell(pl.sector)} | {pl.role} | "
                    f"{_md_cell(pl.parent_platform or '—')} | "
                    f"{_md_cell(', '.join(pl.naics_codes) or '—')} |  |  |"
                )
        lines.append(
            "\nfields checked: ___ · wrong: ___ · portfolio rows checked: ___ · wrong: ___"
        )

    lines.append(
        "\n---\n\n## Tally\n\n"
        "| | checked | wrong | accuracy |\n| --- | --- | --- | --- |\n"
        "| criteria fields | | | |\n| portfolio NAICS | | | |\n| **total** | | | |\n\n"
        "Notes on any systematic failure (same mistake across several firms):\n"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_check(args) -> int:
    rows = load_index(Path(args.db))
    if rows is None:
        return 1
    if not rows:
        return 1

    issues = run_checks(rows)
    summary = summarize(rows, issues)

    table = Table(title="index consistency checks", show_header=True, header_style="bold")
    for col in ("check", "sev", "hits", "funds", "example"):
        table.add_column(col)
    for code, s in summary["checks"].items():
        hits = s["count"]
        style = "" if hits == 0 else ("red" if s["severity"] == "error" else "yellow")
        example = s["examples"][0][:64] if s["examples"] else ""
        table.add_row(code, s["severity"], str(hits), str(s["funds_affected"]), example,
                      style=style)
    console.print(table)
    console.print(
        f"{summary['funds_checked']} funds · {summary['platforms_checked']} platforms · "
        f"[bold]{summary['funds_with_no_issues']}[/bold] clean "
        f"({summary['clean_rate']:.0%}) · {summary['errors']} errors · "
        f"{summary['warnings']} warnings"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ISSUES_PATH.write_text(
        json.dumps({"summary": summary, "issues": [asdict(i) for i in issues]}, indent=2)
    )
    console.print(f"detail: {ISSUES_PATH}")

    # Nonzero only on request, so the report itself is always readable in CI.
    return 1 if (args.fail_on_error and summary["errors"]) else 0


def cmd_sample(args) -> int:
    rows = load_index(Path(args.db))
    if rows is None:
        return 1
    if not rows:
        return 1

    sample = stratified_sample(rows, args.n, args.seed)
    md = render_worksheet(sample, args.seed, len(rows), with_pages=not args.no_cached_pages)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)

    table = Table(title=f"sample of {len(sample)} (seed {args.seed})", header_style="bold")
    for col in ("#", "firm", "tier", "stratum", "platforms", "ebitda band"):
        table.add_column(col)
    for i, row in enumerate(sample, 1):
        p: FundProfile = row["profile"]
        depth, band = _stratum(row)
        ebitda = (
            f"{_usd(p.ebitda_min_usd)}–{_usd(p.ebitda_max_usd)}"
            if p.ebitda_min_usd is not None else "—"
        )
        table.add_row(str(i), p.firm_name[:34], row["tier"], f"{depth}/{band}",
                      str(len(p.platforms)), ebitda)
    console.print(table)
    console.print(f"worksheet: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="buyerlist.index.qa",
        description="Quality assurance for the scraped fund index.",
    )
    ap.add_argument("--db", default=str(DB_PATH), help="index database (opened read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="write a human verification worksheet")
    s.add_argument("--n", type=int, default=20, help="funds to sample (default 20)")
    s.add_argument("--seed", type=int, default=7, help="sampling seed; same seed = same sample")
    s.add_argument("--out", default=str(WORKSHEET_PATH))
    s.add_argument("--no-cached-pages", action="store_true",
                   help="skip the fetch-cache scan for extra source URLs")
    s.set_defaults(func=cmd_sample)

    c = sub.add_parser("check", help="automated consistency scan of the whole index")
    c.add_argument("--fail-on-error", action="store_true",
                   help="exit 1 if any error-severity issue is found")
    c.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
