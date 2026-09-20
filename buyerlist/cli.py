"""Command line entry point.

    python -m buyerlist run https://example.com
    python -m buyerlist index stats
    python -m buyerlist index build
"""

from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import urlparse

from rich.console import Console
from rich.table import Table

from .extract import extract_company, estimate_size
from .fetch import fetch_site_sync
from .index.store import connect, stats
from .llm import LLM, Telemetry
from .match import critique, hard_filter, rank
from .memo import render_markdown, write_outputs

console = Console()


# A trailing legal suffix is formatting, not identity: "Alpine Investors" and
# "Alpine Investors LP" are one firm. Small and local by design — the runtime
# path must not import from the backtest harness, which keeps a fuller list.
_LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "llp", "lp", "ltd", "limited",
                   "co", "corp", "corporation", "company", "plc", "pllc"}


def _norm_name(name: str | None) -> str:
    """Casefold and flatten punctuation so formatting drift is not a hallucination."""
    tokens = re.findall(r"[a-z0-9]+", (name or "").casefold())
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def _split_hallucinations(matches, candidates) -> tuple[list, list, list]:
    """(kept, out_of_index_firms, out_of_index_platforms).

    The model may only name firms and platforms we gave it. The platform half
    is the load-bearing one: addon_thesis is the heaviest scoring dimension and
    the thesis runs through the platform name, so an invented platform is an
    invented thesis. Such a match is dropped rather than downgraded with
    via_platform cleared — what would survive is a rationale and an angle built
    on a company that does not exist, which is worse than an absent match.
    """
    allowed_firms = {_norm_name(r["profile"].firm_name) for r in candidates}
    allowed_platforms = {
        _norm_name(p.name) for r in candidates for p in r["profile"].platforms
    }
    kept, bad_firms, bad_platforms = [], [], []
    for m in matches:
        if _norm_name(m.firm_name) not in allowed_firms:
            bad_firms.append(m)
        elif m.via_platform and _norm_name(m.via_platform) not in allowed_platforms:
            bad_platforms.append(m)
        else:
            kept.append(m)
    return kept, bad_firms, bad_platforms


def _slug(url: str) -> str:
    host = urlparse(url if url.startswith("http") else "https://" + url).netloc
    return re.sub(r"[^a-z0-9]+", "-", host.lower().removeprefix("www.")).strip("-")


def cmd_run(args) -> int:
    tel = Telemetry()
    llm = LLM(tel)
    conn = connect()

    idx = stats(conn)
    if idx["funds"] == 0:
        console.print("[red]Fund index is empty. Run `index build` first.[/red]")
        return 1

    console.rule(f"[bold]{args.url}")
    console.print(f"index: {idx['funds']} funds · {idx['platforms']} platforms "
                  f"· {idx['add_ons']} add-ons")

    with console.status("fetching site..."):
        snap = fetch_site_sync(args.url, "company", max_pages=args.max_pages)
    if not snap.pages:
        console.print(f"[red]Could not read site:[/red] {'; '.join(snap.notes)}")
        return 1
    console.print(f"fetched {len(snap.pages)} pages "
                  f"({len(snap.to_prompt_text()):,} chars)"
                  + (f"  [yellow]{'; '.join(snap.notes)}[/yellow]" if snap.notes else ""))

    with console.status("extracting company profile..."):
        profile = extract_company(llm, snap)
    console.print(f"[green]{profile.legal_name}[/green] — "
                  f"{', '.join(n.code for n in profile.naics_codes)} — "
                  f"{profile.hq_city}, {profile.hq_state}")

    with console.status("estimating size from proxies..."):
        size = estimate_size(llm, profile)
    console.print(f"EBITDA ${size.ebitda_low_usd:,}–${size.ebitda_high_usd:,} "
                  f"({size.confidence} confidence)")

    candidates, diag = hard_filter(conn, profile, size)
    console.print(f"filtered {diag['universe']} → {diag['kept']} candidates  "
                  f"(dropped: {diag['dropped']})")
    if not candidates:
        console.print("[red]No candidates survived the filter.[/red]")
        return 1

    with console.status(f"scoring {len(candidates)} candidates..."):
        ranked = rank(llm, conn, profile, size, candidates)

    # Guardrail: nothing the model invented may reach the memo.
    kept, dropped_firms, dropped_platforms = _split_hallucinations(
        ranked.matches, candidates
    )
    if dropped_firms:
        console.print(f"[red]dropped {len(dropped_firms)} out-of-index name(s): "
                      f"{', '.join(m.firm_name for m in dropped_firms)}[/red]")
    if dropped_platforms:
        named = ", ".join(f"{m.firm_name} via {m.via_platform}"
                          for m in dropped_platforms)
        console.print(f"[red]dropped {len(dropped_platforms)} match(es) citing an "
                      f"out-of-index platform: {named}[/red]")
    ranked.matches = kept

    with console.status("running adversarial critique..."):
        crit = critique(llm, profile, size, ranked)

    table = Table(show_header=True, header_style="bold")
    for col in ("#", "firm", "via platform", "tier", "score"):
        table.add_column(col)
    for i, m in enumerate(ranked.matches[:12], 1):
        table.add_row(str(i), m.firm_name[:34], (m.via_platform or "—")[:28],
                      m.tier, str(m.score))
    console.print(table)

    md = render_markdown(url=args.url, profile=profile, size=size, ranked=ranked,
                         crit=crit, diagnostics=diag, telemetry=tel)
    payload = {
        "url": args.url,
        "profile": profile.model_dump(),
        "size": size.model_dump(),
        "diagnostics": diag,
        "matches": [m.model_dump() for m in ranked.matches],
        "critique": crit.model_dump(),
        "cost_usd": tel.total_cost_usd,
    }
    md_path, js_path = write_outputs(_slug(args.url), md, payload)

    console.print(f"\n[bold]${tel.total_cost_usd:.4f}[/bold] · "
                  f"{tel.total_seconds:.0f}s model time")
    console.print(f"memo: {md_path}\njson: {js_path}")
    return 0


def cmd_index(args) -> int:
    conn = connect()
    if args.index_cmd == "stats":
        s = stats(conn)
        t = Table(show_header=False)
        for k, v in s.items():
            t.add_row(k, str(v))
        console.print(t)
        return 0
    if args.index_cmd == "build":
        from .index.build import build
        llm = LLM()
        s = build(llm, limit=args.limit)
        console.print(s)
        console.print(f"cost ${llm.telemetry.total_cost_usd:.2f}")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="buyerlist")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="generate a buyer list for a company URL")
    r.add_argument("url")
    r.add_argument("--max-pages", type=int, default=12)
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("index", help="fund index operations")
    i.add_argument("index_cmd", choices=["stats", "build"])
    i.add_argument("--limit", type=int, default=None)
    i.set_defaults(func=cmd_index)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
