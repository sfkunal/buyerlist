"""Render the buyer-list memo.

The deliverable is not a ranked list, it is the document an analyst would put in
front of a seller: tiered buyers, a thesis and an opening angle for each, and —
the column that actually gets read — why each one might not work.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .llm import Telemetry
from .schemas import CompanyProfile, Critique, RankedMatches, SizeEstimate

TIER_TITLES = {
    "tier_1": "Tier 1 — specific thesis",
    "tier_2": "Tier 2 — plausible fit",
    "wildcard": "Wildcards",
}


def _usd(n: int) -> str:
    if n >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    return f"${n:,}"


def render_markdown(
    *,
    url: str,
    profile: CompanyProfile,
    size: SizeEstimate,
    ranked: RankedMatches,
    crit: Critique,
    diagnostics: dict,
    telemetry: Telemetry,
) -> str:
    objections = {d.firm_name: d for d in crit.disqualifiers}
    out: list[str] = []
    a = out.append

    a(f"# Buyer list — {profile.legal_name}")
    a("")
    a(f"*{url} · generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    a("")

    # ---- company ----
    a("## The business")
    a("")
    a(profile.description)
    a("")
    a(f"- **Location** — {profile.hq_city or '?'}, {profile.hq_state or '?'}"
      + (f" · serving {', '.join(profile.service_area[:6])}" if profile.service_area else ""))
    a(f"- **Sector** — " + ", ".join(f"{n.code} {n.label}" for n in profile.naics_codes))
    a(f"- **Model** — {', '.join(profile.business_model) or 'unclear'}"
      + (f" · {', '.join(profile.end_markets)}" if profile.end_markets else ""))
    if profile.year_founded:
        a(f"- **Founded** — {profile.year_founded}"
          + (f" · {profile.locations_count} locations" if profile.locations_count else ""))
    if profile.ownership_signals:
        a(f"- **Ownership** — {', '.join(profile.ownership_signals)}")
    a("")

    # ---- size ----
    a("## Size estimate")
    a("")
    a(f"**Revenue {_usd(size.revenue_low_usd)}–{_usd(size.revenue_high_usd)} · "
      f"EBITDA {_usd(size.ebitda_low_usd)}–{_usd(size.ebitda_high_usd)}** "
      f"at an assumed {size.margin_assumption_pct:.0f}% margin · confidence: {size.confidence}")
    a("")
    a(f"No financials are published. This is inferred from website proxies. {size.comparable_basis}")
    a("")
    if size.signals_used:
        a("Signals used:")
        a("")
        for s in size.signals_used:
            a(f"- {s}")
        a("")
    if size.caveats:
        a("What would most change this:")
        a("")
        for c in size.caveats[:5]:
            a(f"- {c}")
        a("")

    # ---- buyers ----
    a("## Buyer shortlist")
    a("")
    d = diagnostics
    a(f"Screened **{d['universe']} funds** in the index down to **{d['kept']}** candidates "
      f"on size, sector, geography and stated exclusions, then scored every survivor.")
    a("")

    for tier in ("tier_1", "tier_2", "wildcard"):
        group = [m for m in ranked.matches if m.tier == tier]
        if not group:
            continue
        a(f"### {TIER_TITLES[tier]}")
        a("")
        for m in group:
            head = f"**{m.firm_name}**"
            if m.via_platform:
                head += f" — via {m.via_platform}"
            a(f"{head}  ·  score {m.score}/100  ·  confidence {m.confidence}")
            a("")
            a(m.rationale)
            a("")
            a(f"*Angle:* {m.angle}")
            a("")
            if m.evidence:
                a(f"*Evidence:* {'; '.join(m.evidence[:4])}")
                a("")
            obj = objections.get(m.firm_name)
            if obj:
                a(f"> **Against ({obj.severity}):** {obj.objection}")
                a("")

    if ranked.excluded_note:
        a("### Considered and dropped")
        a("")
        a(ranked.excluded_note)
        a("")

    if crit.overall_note:
        a("### Reviewer note")
        a("")
        a(crit.overall_note)
        a("")

    # ---- provenance ----
    a("## Provenance")
    a("")
    a("Every claim about the business traces to a quote on the company's own site:")
    a("")
    for e in profile.evidence[:10]:
        a(f"- **{e.field}** — \"{e.quote[:150]}\"  \n  <{e.source_url}>")
    a("")
    if profile.risk_flags:
        a("Flags a buyer would diligence:")
        a("")
        for r in profile.risk_flags:
            a(f"- {r}")
        a("")
    if profile.notes:
        a(f"*Not determinable from the site: {profile.notes}*")
        a("")

    # ---- cost ----
    a("## Run cost")
    a("")
    a("| stage | model | in | out | cache | cost |")
    a("|---|---|---|---|---|---|")
    for row in telemetry.summary_rows():
        a("| " + " | ".join(row) + " |")
    a(f"| **total** | | | | | **${telemetry.total_cost_usd:.4f}** |")
    a("")
    a(f"Wall clock: {telemetry.total_seconds:.0f}s of model time.")
    a("")
    return "\n".join(out)


def write_outputs(
    slug: str,
    markdown: str,
    payload: dict,
    out_dir: Path = Path("out"),
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{slug}.md"
    js_path = out_dir / f"{slug}.json"
    md_path.write_text(markdown)
    js_path.write_text(json.dumps(payload, indent=2, default=str))
    return md_path, js_path
