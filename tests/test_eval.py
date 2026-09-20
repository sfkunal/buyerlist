"""Backtest plumbing: metrics, deal parsing, and rank lookup.

`run_eval._self_test()` already covers name normalisation, the leakage guard and
the hallucination split with 35 offline assertions, and it is kept as the
dependency-free smoke path (`python -m buyerlist.evalset.run_eval --self-test`).
It is adopted here rather than duplicated, so one `pytest` run covers both.

What this module adds is the surface `_self_test` does not reach: the metric
arithmetic that turns per-deal records into the numbers a reader will quote, and
the deal-file parsing that feeds it.
"""

from __future__ import annotations

import json

import pytest

from buyerlist.evalset.run_eval import (
    RECALL_KS,
    Deal,
    _month_key,
    _self_test,
    compute_metrics,
    load_deals,
    rank_of,
)


def rec(
    *,
    rank=None,
    in_index=True,
    error=None,
    survived=True,
    removed=0,
    named_platforms=(),
    named_addons=(),
) -> dict:
    """One per-deal record, shaped as `score_deal` returns it."""
    return {
        "rank": rank,
        "acquirer_in_index": in_index,
        "error": error,
        "survived_filter": survived,
        "guard": {
            "total_removed": removed,
            "platforms_named_target": list(named_platforms),
            "addons_named_target": list(named_addons),
        },
    }


# --------------------------------------------------------------------------
# The existing offline suite, run as part of this one
# --------------------------------------------------------------------------


def test_run_eval_self_test_passes():
    assert _self_test() == 0


# --------------------------------------------------------------------------
# compute_metrics
# --------------------------------------------------------------------------


def test_dry_run_reports_rank_figures_as_null_not_zero():
    # Nothing was ranked in a dry run, so a 0% would read as a measured failure
    # of the ranker. This distinction is the whole reason the flag is threaded
    # through the metric function.
    m = compute_metrics([rec(in_index=True), rec(in_index=False)], universe=150, dry_run=True)

    assert all(m["recall_all"][f"@{k}"] is None for k in RECALL_KS)
    assert all(m["recall_in_index"][f"@{k}"] is None for k in RECALL_KS)
    assert m["found_n"] is None
    assert m["survived_filter_n"] is None
    # Coverage is measurable without ranking, so it is still reported.
    assert m["coverage_n"] == 1
    assert m["coverage_pct"] == pytest.approx(0.5)


def test_recall_counts_a_hit_at_or_inside_k():
    m = compute_metrics([rec(rank=5), rec(rank=6)], universe=100)
    assert m["recall_all"]["@5"] == pytest.approx(0.5)   # rank 5 is inside @5
    assert m["recall_all"]["@10"] == pytest.approx(1.0)


def test_unranked_deals_count_against_recall_but_errored_ones_do_not():
    # A dead target website measures the crawler, not the ranker, so it leaves
    # the denominator. A deal that ranked nowhere is a real ranking miss.
    records = [rec(rank=1), rec(rank=None), rec(error="HTTPError: 500")]
    m = compute_metrics(records, universe=100)

    assert m["deals_total"] == 3
    assert m["deals_scored"] == 2
    assert m["deals_errored"] == 1
    assert m["recall_all_n"] == 2
    assert m["recall_all"]["@10"] == pytest.approx(0.5)


def test_recall_in_index_conditions_on_coverage():
    # Two different failures with two different fixes: an acquirer missing from
    # the index is an index-building problem, and one present but unranked is a
    # ranking problem. The pair of denominators keeps them apart.
    records = [rec(rank=3, in_index=True), rec(rank=None, in_index=False)]
    m = compute_metrics(records, universe=100)

    assert m["recall_all"]["@5"] == pytest.approx(0.5)      # 1 of 2 scored deals
    assert m["recall_in_index"]["@5"] == pytest.approx(1.0)  # 1 of 1 covered deals
    assert m["recall_in_index_n"] == 1


def test_median_and_mean_rank_use_only_the_deals_that_ranked():
    m = compute_metrics([rec(rank=2), rec(rank=4), rec(rank=None)], universe=100)
    assert m["median_rank"] == 3
    assert m["mean_rank"] == pytest.approx(3.0)
    assert m["found_n"] == 2


def test_rank_figures_are_null_when_nothing_ranked():
    m = compute_metrics([rec(rank=None)], universe=100)
    assert m["median_rank"] is None
    assert m["mean_rank"] is None


def test_universe_size_is_carried_into_the_metrics():
    # Recall@20 out of 60 funds is a weak claim and recall@20 out of 2,000 is a
    # strong one, so the denominator travels with the number.
    assert compute_metrics([rec(rank=1)], universe=163)["universe_funds"] == 163


def test_guard_activity_is_totalled_and_direct_leaks_counted_separately():
    records = [
        rec(removed=10, named_platforms=["Fund A: Target Co"]),
        rec(removed=5, named_addons=["Fund B/Platform: Target Co"]),
    ]
    m = compute_metrics(records, universe=100)

    assert m["guard_removals_total"] == 15
    # Direct leaks are the entries that name the target itself — the ones that
    # would have handed the answer to the ranker.
    assert m["guard_direct_leaks"] == 2


def test_no_deals_reports_null_coverage_rather_than_dividing_by_zero():
    m = compute_metrics([], universe=100)
    assert m["coverage_pct"] is None
    assert m["deals_total"] == 0


def test_recall_is_null_when_every_deal_errored():
    # An empty denominator is unmeasured, not zero.
    m = compute_metrics([rec(error="boom")], universe=100)
    assert m["recall_all"]["@10"] is None


# --------------------------------------------------------------------------
# rank_of
# --------------------------------------------------------------------------


class FakeMatch:
    def __init__(self, firm_name):
        self.firm_name = firm_name


def test_rank_of_is_one_based():
    matches = [FakeMatch("Alpine Investors"), FakeMatch("Trivest Partners")]
    assert rank_of(matches, "Trivest Partners") == 2


def test_rank_of_tolerates_legal_suffix_drift():
    # The eval set and the index rarely spell a firm the same way.
    assert rank_of([FakeMatch("Trivest Partners, LLC")], "Trivest Partners") == 1


def test_rank_of_returns_none_when_absent():
    assert rank_of([FakeMatch("Alpine Investors")], "Trivest Partners") is None


def test_rank_of_on_an_empty_ranking():
    assert rank_of([], "Trivest Partners") is None


# --------------------------------------------------------------------------
# Deal date handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "announced,month,year",
    [
        ("2023-07", "2023-07", 2023),
        ("2023/07", "2023-07", 2023),
        ("202307", "2023-07", 2023),
        ("2023", "2023-12", 2023),
        (None, None, None),
        ("unknown", None, None),
    ],
)
def test_deal_normalises_its_announcement_date(announced, month, year):
    deal = Deal(acquirer_fund="F", target="T", target_url="u", announced=announced)
    assert deal.announced_month == month
    assert deal.announced_year == year


def test_bare_year_cutoffs_are_deliberately_asymmetric():
    # A deal known only to a year takes December as its cutoff, while an add-on
    # known only to a year takes January. Same-year history is therefore kept
    # rather than guessed at, which matches the docstring's "year granularity is
    # all the index has" — the guard would rather keep a little pre-deal history
    # than delete real signal on a month it cannot actually read.
    deal = Deal(acquirer_fund="F", target="T", target_url="u", announced="2023")
    assert deal.announced_month == "2023-12"
    assert _month_key("2023") == "2023-01"
    assert _month_key("2023") <= deal.announced_month


# --------------------------------------------------------------------------
# load_deals
# --------------------------------------------------------------------------


def test_load_deals_reads_one_deal_per_line(tmp_path):
    path = tmp_path / "deals.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(d)
            for d in [
                {"acquirer_fund": "Trivest Partners", "target": "DMI", "target_url": "https://a"},
                {"acquirer_fund": "Alpine Investors", "target": "Apex", "target_url": "https://b"},
            ]
        )
    )
    deals = load_deals(path)
    assert [d.acquirer_fund for d in deals] == ["Trivest Partners", "Alpine Investors"]


def test_load_deals_ignores_blanks_and_comments(tmp_path):
    path = tmp_path / "deals.jsonl"
    path.write_text(
        '// sourced from press releases\n'
        '\n'
        '# still a comment\n'
        '{"acquirer_fund": "F", "target": "T", "target_url": "https://a"}\n'
    )
    assert len(load_deals(path)) == 1


def test_load_deals_skips_a_malformed_line_without_losing_the_rest(tmp_path):
    # The eval set is written by a separate process, so this may run against a
    # file that is half-written. One bad line must not cost the other 53 deals.
    path = tmp_path / "deals.jsonl"
    path.write_text(
        '{"acquirer_fund": "Good", "target": "T", "target_url": "https://a"}\n'
        '{"acquirer_fund": "Truncated", "targ\n'
        '{"acquirer_fund": "AlsoGood", "target": "T", "target_url": "https://b"}\n'
    )
    assert [d.acquirer_fund for d in load_deals(path)] == ["Good", "AlsoGood"]


def test_load_deals_tolerates_extra_keys(tmp_path):
    # Provenance columns accumulate in the source file; unknown keys are
    # dropped rather than raising.
    path = tmp_path / "deals.jsonl"
    path.write_text(
        json.dumps(
            {
                "acquirer_fund": "F",
                "target": "T",
                "target_url": "https://a",
                "enterprise_value": "undisclosed",
                "scraped_by": "someone",
            }
        )
    )
    assert load_deals(path)[0].acquirer_fund == "F"


def test_load_deals_on_a_missing_file_returns_empty(tmp_path):
    assert load_deals(tmp_path / "nope.jsonl") == []
