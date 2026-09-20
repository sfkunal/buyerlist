"""Rendering: the cached index prefix, the ranking prompt, and the memo.

Three pure string builders, each of which fails quietly rather than loudly.
`compact_line` sets the floor on per-run cost because it is the cached prefix of
every ranking call; `_target_block` is the text the ranker actually reasons
over; `render_markdown` is the deliverable. A regression in any of them changes
the answer or the bill without raising.
"""

from __future__ import annotations

from buyerlist.index.store import _usd as index_usd
from buyerlist.index.store import compact_line
from buyerlist.match import _target_block
from buyerlist.memo import _usd as memo_usd
from buyerlist.memo import render_markdown
from buyerlist.llm import Telemetry
from buyerlist.schemas import Critique, Disqualifier, Match, RankedMatches

from .conftest import make_addon, make_company, make_fund, make_platform, make_size


def make_match(firm_name="Alpine Investors", **kw) -> Match:
    return Match(
        **{
            "firm_name": firm_name,
            "via_platform": "Apex Service Partners",
            "dimension_scores": [],
            "score": 82,
            "tier": "tier_1",
            "rationale": "Owns an HVAC roll-up doing tuck-ins in adjacent states.",
            "angle": "Position as an add-on, not a platform.",
            "evidence": ["Apex has acquired four HVAC businesses since 2023"],
            "confidence": "high",
            **kw,
        }
    )


def render(matches, disqualifiers=(), **kw) -> str:
    return render_markdown(
        url="https://hoffmannbros.com",
        profile=kw.pop("profile", make_company()),
        size=kw.pop("size", make_size()),
        ranked=RankedMatches(matches=list(matches), excluded_note=kw.pop("excluded_note", "")),
        crit=Critique(
            disqualifiers=list(disqualifiers), overall_note=kw.pop("overall_note", "")
        ),
        diagnostics=kw.pop("diagnostics", {"universe": 163, "kept": 40, "dropped": {}}),
        telemetry=kw.pop("telemetry", Telemetry()),
    )


# --------------------------------------------------------------------------
# store._usd and compact_line — the cached prefix
# --------------------------------------------------------------------------


def test_index_usd_abbreviates_by_magnitude():
    assert index_usd(3_000_000) == "3M"
    assert index_usd(500_000) == "500K"
    assert index_usd(None) == "?"


def test_index_usd_rounds_to_whole_units():
    # Terseness is the point here — this text is the cached prefix of every
    # ranking call, so its size sets the floor on per-run cost.
    assert index_usd(1_500_000) == "2M"
    assert index_usd(2_400_000) == "2M"


def test_compact_line_carries_the_fields_the_filter_and_rubric_need():
    fund = make_fund(
        "Alpine Investors",
        ebitda_min_usd=3_000_000,
        ebitda_max_usd=25_000_000,
        exclusions=["no real estate"],
    )
    line = compact_line({"profile": fund})

    assert line.startswith("[Alpine Investors]")
    assert "EBITDA 3M-25M" in line
    assert "geo=North America" in line
    assert "naics=238" in line
    assert "excludes=no real estate" in line


def test_compact_line_renders_portfolio_with_addon_counts_and_states():
    # Platform and add-on history is what makes an add-on thesis possible, so it
    # earns its tokens even in the compact view. The states are the geographic
    # half of that thesis.
    fund = make_fund(
        platforms=[
            make_platform(
                "Apex Service Partners",
                add_ons=[make_addon("A", state="MO"), make_addon("B", state="IL")],
            )
        ]
    )
    line = compact_line({"profile": fund})

    assert "portfolio: Apex Service Partners[HVAC/238220] +2(IL,MO)" in line


def test_compact_line_labels_an_add_on_with_its_parent():
    fund = make_fund(
        platforms=[
            make_platform("Tuck-In Co", role="add_on", parent_platform="Apex Service Partners")
        ]
    )
    assert "ADD-ON of Apex Service Partners" in compact_line({"profile": fund})


def test_compact_line_omits_the_portfolio_section_when_there_is_none():
    assert "portfolio:" not in compact_line({"profile": make_fund(platforms=[])})


def test_compact_line_truncates_long_lists_to_stay_cheap():
    fund = make_fund(geographies=[f"Region {i}" for i in range(10)])
    assert compact_line({"profile": fund}).count("Region") == 3


def test_compact_line_marks_unknown_bands_rather_than_inventing_them():
    # Roughly two thirds of firms publish no band. That has to read as unknown
    # to the ranker, which is told not to penalise a firm for it.
    assert "EBITDA ?-?" in compact_line({"profile": make_fund()})


# --------------------------------------------------------------------------
# match._target_block — the text the ranker reasons over
# --------------------------------------------------------------------------


def test_target_block_states_the_size_estimate_and_its_midpoint():
    # The rubric tells the model to score size_fit against the midpoint, so the
    # midpoint has to actually be in the prompt.
    block = _target_block(make_company(), make_size())

    assert "Midpoint EBITDA: $6,000,000" in block
    assert "$4,000,000 - $8,000,000" in block
    assert "assumed 12.0% margin" in block


def test_target_block_labels_the_estimate_as_inferred_not_reported():
    block = _target_block(make_company(), make_size())
    assert "derived from website proxies, not financials" in block


def test_target_block_renders_missing_fields_as_unknown():
    profile = make_company(hq_city=None, hq_state=None, year_founded=None, service_area=[])
    block = _target_block(profile, make_size())

    assert "Location: ?, ?" in block
    assert "Service area: unknown" in block
    assert "Founded: unknown" in block


# --------------------------------------------------------------------------
# memo.render_markdown — the deliverable
# --------------------------------------------------------------------------


def test_memo_usd_abbreviates_millions_with_one_decimal():
    assert memo_usd(4_800_000) == "$4.8M"
    assert memo_usd(950_000) == "$950,000"


def test_memo_groups_matches_under_their_tier_headings():
    out = render([make_match(tier="tier_1"), make_match("Trivest Partners", tier="wildcard")])

    assert "### Tier 1 — specific thesis" in out
    assert "### Wildcards" in out
    # An empty tier prints no heading at all.
    assert "Tier 2" not in out


def test_memo_names_the_platform_a_thesis_runs_through():
    out = render([make_match()])
    assert "**Alpine Investors** — via Apex Service Partners" in out


def test_memo_renders_a_direct_investment_without_a_platform_clause():
    out = render([make_match(via_platform=None)])
    assert "**Alpine Investors**" in out
    assert "— via" not in out


def test_memo_attaches_the_objection_to_its_match():
    # "Why this might not work" is the column a banker actually reads, so it has
    # to land inline with the match rather than in a separate section.
    out = render(
        [make_match("Alpine Investors")],
        [Disqualifier(firm_name="Alpine Investors", objection="Fund is late in its life.",
                      severity="material")],
    )
    assert "> **Against (material):** Fund is late in its life." in out


def test_memo_matches_objections_on_the_exact_firm_name():
    # Pins current behaviour: the objection lookup is an exact dict hit on
    # firm_name, not the normalised comparison used by the hallucination guard.
    # A critique that returns "Alpine Investors, LP" for a match named "Alpine
    # Investors" therefore renders no objection — silently, since a missing key
    # is indistinguishable from "the reviewer had nothing to say".
    out = render(
        [make_match("Alpine Investors")],
        [Disqualifier(firm_name="Alpine Investors, LP", objection="Size mismatch.",
                      severity="fatal")],
    )
    assert "Size mismatch." not in out


def test_memo_reports_the_screen_that_produced_the_shortlist():
    out = render([make_match()], diagnostics={"universe": 163, "kept": 40, "dropped": {}})
    assert "Screened **163 funds**" in out
    assert "down to **40** candidates" in out


def test_memo_carries_provenance_for_every_claim():
    out = render([make_match()])
    assert "## Provenance" in out
    assert "serving St. Louis since 1950" in out
    assert "https://example.com/about" in out


def test_memo_states_what_could_not_be_determined():
    out = render([make_match()], profile=make_company(notes="Headcount not published."))
    assert "Not determinable from the site: Headcount not published." in out


def test_memo_always_prints_a_cost_table():
    out = render([make_match()])
    assert "## Run cost" in out
    assert "| **total** |" in out


def test_memo_renders_with_no_matches_at_all():
    # A run that survives the filter but ranks nothing must still produce a
    # readable document rather than a traceback.
    out = render([])
    assert "# Buyer list — Hoffmann Brothers" in out
    assert "## Buyer shortlist" in out
