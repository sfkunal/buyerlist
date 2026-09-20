"""Run-cost telemetry.

The project's headline claim is a dollar figure, and `llm.py` says that figure
should be measured rather than asserted. That makes the arithmetic below a
load-bearing part of the argument, not instrumentation — if it is wrong, the
claim is wrong and nothing else in the run reveals it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from buyerlist.llm import CACHE_WRITE_MULTIPLIER, PRICES, StageUsage, Telemetry

MILLION = 1_000_000


def usage(inp=0, out=0, cache_read=0, cache_write=0) -> SimpleNamespace:
    """A stand-in for the SDK's usage object, which `record` reads by getattr."""
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


# --------------------------------------------------------------------------
# Per-token arithmetic
# --------------------------------------------------------------------------


def test_input_and_output_bill_at_the_published_rates():
    s = StageUsage(stage="rank", model="claude-opus-5", input_tokens=MILLION)
    assert s.cost_usd == pytest.approx(5.00)

    s = StageUsage(stage="rank", model="claude-opus-5", output_tokens=MILLION)
    assert s.cost_usd == pytest.approx(25.00)


def test_cache_reads_bill_at_a_tenth_of_input():
    s = StageUsage(stage="rank", model="claude-opus-5", cache_read_tokens=MILLION)
    assert s.cost_usd == pytest.approx(0.50)


def test_cache_write_multiplier_regression_follows_the_requested_ttl():
    # REGRESSION. The multiplier was hardcoded at 1.25 ("5m"), but the ranking
    # call asks for a 1-hour cache, which bills at 2x. The fund index is the
    # largest cached prefix in the project, so the old constant understated
    # first-run cost exactly where the central cost claim lives.
    five_min = StageUsage(
        stage="rank", model="claude-opus-5", cache_write_tokens=MILLION, cache_ttl="5m"
    )
    one_hour = StageUsage(
        stage="rank", model="claude-opus-5", cache_write_tokens=MILLION, cache_ttl="1h"
    )

    assert five_min.cost_usd == pytest.approx(6.25)   # 5.00 * 1.25
    assert one_hour.cost_usd == pytest.approx(10.00)  # 5.00 * 2.00
    assert one_hour.cost_usd > five_min.cost_usd


def test_cache_ttl_defaults_to_the_api_default():
    # The API's own default is the 5-minute cache, so a stage that does not
    # pass a TTL must not be priced as though it asked for the hour.
    assert StageUsage(stage="x", model="claude-opus-5").cache_ttl == "5m"
    assert set(CACHE_WRITE_MULTIPLIER) == {"5m", "1h"}


def test_a_stage_sums_all_four_token_classes():
    s = StageUsage(
        stage="rank",
        model="claude-opus-5",
        input_tokens=MILLION,
        output_tokens=MILLION,
        cache_read_tokens=MILLION,
        cache_write_tokens=MILLION,
        cache_ttl="1h",
    )
    assert s.cost_usd == pytest.approx(5.00 + 25.00 + 0.50 + 10.00)


@pytest.mark.parametrize("model", sorted(PRICES))
def test_every_priced_model_costs_something(model):
    assert StageUsage(stage="x", model=model, input_tokens=MILLION).cost_usd > 0


def test_unknown_model_raises_rather_than_pricing_at_zero():
    # REGRESSION. A `.get(model, (0.0, 0.0))` default reported an unrecognised
    # model as free, which is the one failure a cost-reporting module must not
    # have: the run looks cheap instead of looking broken.
    s = StageUsage(stage="rank", model="claude-not-a-model", input_tokens=MILLION)
    with pytest.raises(KeyError) as exc:
        _ = s.cost_usd
    assert "claude-not-a-model" in str(exc.value)


def test_a_stage_with_no_tokens_is_free():
    assert StageUsage(stage="x", model="claude-opus-5").cost_usd == 0.0


# --------------------------------------------------------------------------
# Aggregation across stages
# --------------------------------------------------------------------------


def test_record_merges_repeated_calls_to_the_same_stage():
    tel = Telemetry()
    tel.record("extract", "claude-sonnet-5", usage(inp=1000, out=100), seconds=1.5)
    tel.record("extract", "claude-sonnet-5", usage(inp=2000, out=200), seconds=2.5)

    assert len(tel.stages) == 1
    stage = tel.stages[0]
    assert stage.calls == 2
    assert stage.input_tokens == 3000
    assert stage.output_tokens == 300
    assert stage.seconds == pytest.approx(4.0)


def test_record_keeps_stages_separate_by_model_and_ttl():
    # Same stage name billed against a different model or a different cache TTL
    # is a different price, so it must not be folded into one row.
    tel = Telemetry()
    tel.record("index.extract", "claude-haiku-4-5", usage(inp=10), seconds=0.1)
    tel.record("index.extract", "claude-sonnet-5", usage(inp=10), seconds=0.1)
    tel.record("rank", "claude-opus-5", usage(inp=10), seconds=0.1, cache_ttl="1h")
    tel.record("rank", "claude-opus-5", usage(inp=10), seconds=0.1, cache_ttl="5m")

    assert len(tel.stages) == 4


def test_totals_sum_across_stages():
    tel = Telemetry()
    tel.record("size", "claude-opus-5", usage(inp=MILLION), seconds=3.0)
    tel.record("extract", "claude-sonnet-5", usage(inp=MILLION), seconds=2.0)

    assert tel.total_cost_usd == pytest.approx(5.00 + 3.00)
    assert tel.total_seconds == pytest.approx(5.0)


def test_totals_on_an_empty_run_are_zero():
    tel = Telemetry()
    assert tel.total_cost_usd == 0.0
    assert tel.total_seconds == 0.0


def test_record_tolerates_a_usage_object_missing_cache_fields():
    # Not every response carries cache counters; a missing attribute must read
    # as zero rather than raising in the middle of a paid run.
    tel = Telemetry()
    tel.record("extract", "claude-sonnet-5", SimpleNamespace(input_tokens=10), seconds=0.1)
    assert tel.stages[0].cache_read_tokens == 0
    assert tel.stages[0].output_tokens == 0


def test_record_tolerates_none_valued_token_counters():
    tel = Telemetry()
    tel.record("extract", "claude-sonnet-5", usage(inp=None, out=None), seconds=0.1)
    assert tel.stages[0].input_tokens == 0


# --------------------------------------------------------------------------
# The memo's cost table
# --------------------------------------------------------------------------


def test_summary_rows_render_one_row_per_stage_with_the_model_prefix_stripped():
    tel = Telemetry()
    tel.record("rank", "claude-opus-5", usage(inp=1_234_567, out=2_000, cache_read=900), 1.0)
    (stage, model, inp, out, cache, cost), = tel.summary_rows()

    assert stage == "rank"
    assert model == "opus-5"          # "claude-" prefix dropped for the table
    assert inp == "1,234,567"          # thousands separators for readability
    assert out == "2,000"
    assert cache == "900 read"
    assert cost.startswith("$")


def test_summary_rows_show_a_dash_when_nothing_was_served_from_cache():
    tel = Telemetry()
    tel.record("extract", "claude-sonnet-5", usage(inp=100), 1.0)
    assert tel.summary_rows()[0][4] == "—"
