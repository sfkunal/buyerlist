"""The hard filter: pure Python, no model, and the last thing between the index
and the ranking prompt.

These functions decide which funds the model is even allowed to consider, so a
silent failure here does not raise — it quietly changes the answer. Two of the
cases below are regressions for bugs that did exactly that.
"""

from __future__ import annotations

import pytest

from buyerlist.match import (
    _bands_overlap,
    _fund_naics_universe,
    _geo_ok,
    _naics_ok,
    _state_code,
    _states_named,
    _words,
)

from .conftest import make_fund, make_platform


# --------------------------------------------------------------------------
# Word-boundary helpers
# --------------------------------------------------------------------------


def test_words_pads_both_ends_so_substring_tests_become_word_tests():
    # The padding is what lets callers write `f" {term} " in blob` and get a
    # word-boundary test out of a plain substring check.
    assert _words("Texas") == " texas "
    assert _words("New York, NY") == " new york ny "


def test_words_splits_punctuated_abbreviations():
    # "U.S." has to reach the national-terms test as the same token stream
    # that NATIONAL_TERMS produces for "u.s.", or a very common way of
    # writing "nationwide" reads as an unparseable constraint.
    assert _words("U.S.") == " u s "
    assert _words("U.S.A.") == " u s a "


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MO", "mo"),
        ("mo", "mo"),
        ("Missouri", "mo"),
        ("missouri", "mo"),
        ("New York", "ny"),
        ("District of Columbia", "dc"),
        ("Ontario", None),
        ("", None),
    ],
)
def test_state_code_resolves_either_form(raw, expected):
    assert _state_code(raw) == expected


# --------------------------------------------------------------------------
# _states_named — codes are matched only in uppercase, on purpose
# --------------------------------------------------------------------------


def test_states_named_reads_uppercase_codes():
    raw = "Serving MO, KS and AR"
    assert _states_named(raw, _words(raw)) == {"mo", "ks", "ar"}


def test_states_named_reads_full_names_case_insensitively():
    raw = "We invest across missouri and Texas"
    assert _states_named(raw, _words(raw)) == {"mo", "tx"}


def test_states_named_ignores_lowercase_two_letter_words():
    # Half the state codes are also ordinary English words. Matching them
    # case-insensitively would read "primarily in Texas" as a claim about
    # Indiana, so codes are only honoured in their uppercase form.
    raw = "primarily in Texas, or nearby"
    named = _states_named(raw, _words(raw))
    assert named == {"tx"}
    assert "in" not in named and "or" not in named


def test_states_named_known_limitation_lowercase_codes_are_missed():
    # Documented trade-off, asserted so it stays a decision rather than drift:
    # a firm writing its codes in lowercase is not understood. This fails
    # closed (the fund is dropped), which is the safe direction.
    raw = "serving mo, ks and ar"
    assert _states_named(raw, _words(raw)) == set()


# --------------------------------------------------------------------------
# _geo_ok
# --------------------------------------------------------------------------


def test_geo_ok_regression_two_letter_code_does_not_match_inside_a_word():
    # REGRESSION. The original test was `target_state.lower() in blob`, so a
    # target in Indiana passed against any fund whose geography blob contained
    # "industrials" — i.e. the geo filter silently did nothing for IN, OR, ME,
    # OK, DE, LA, MA, MS, PA, AL, AR, MN, MT and MD.
    assert _geo_ok("IN", ["industrials and business services"]) is False
    assert _geo_ok("OR", ["we invest in or around manufacturing"]) is False
    assert _geo_ok("IN", ["primarily in Texas"]) is False


def test_geo_ok_regression_redundant_clause_removed():
    # REGRESSION. The original returned `A or (len(code) == 2 and A)`, which is
    # identically `A`. The dead clause is gone; what remains must still accept a
    # two-letter target that the fund genuinely names.
    assert _geo_ok("MO", ["MO"]) is True
    assert _geo_ok("MO", ["Missouri"]) is True


@pytest.mark.parametrize("national", ["North America", "United States", "U.S.", "nationwide"])
def test_geo_ok_national_language_always_passes(national):
    assert _geo_ok("MO", [national]) is True


def test_geo_ok_rejects_when_other_states_are_named():
    assert _geo_ok("MO", ["Texas", "Oklahoma"]) is False


def test_geo_ok_accepts_when_the_target_is_among_several_named_states():
    assert _geo_ok("KS", ["Serving MO, KS and AR"]) is True


def test_geo_ok_matches_multiword_state_names():
    assert _geo_ok("NY", ["New York"]) is True
    assert _geo_ok("MO", ["New York"]) is False


def test_geo_ok_treats_region_only_geography_as_unreadable_not_contradictory():
    # Firms disagree about which states are "Midwest", so expanding a region to
    # member states would invent a claim the fund never made. A region-only blob
    # therefore passes for everyone rather than rejecting all fifty states.
    assert _geo_ok("MO", ["Midwest"]) is True
    assert _geo_ok("CA", ["Midwest"]) is True


def test_geo_ok_rejects_geography_that_names_neither_state_nor_region():
    assert _geo_ok("MO", ["We back founder-owned businesses"]) is False


@pytest.mark.parametrize(
    "target,geographies",
    [
        ("MO", []),          # fund states no constraint
        (None, ["Texas"]),   # we could not read the target's state
        ("Ontario", ["Texas"]),  # target state is unparseable
    ],
)
def test_geo_ok_missing_information_is_not_evidence_of_mismatch(target, geographies):
    # The filter's contract: exclude only on positive evidence of a mismatch.
    assert _geo_ok(target, geographies) is True


# --------------------------------------------------------------------------
# _bands_overlap
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,fund,expected",
    [
        ((4_000_000, 8_000_000), (3_000_000, 10_000_000), True),   # fund brackets target
        ((4_000_000, 8_000_000), (8_000_000, 20_000_000), True),   # touching at one point
        ((4_000_000, 8_000_000), (9_000_000, 20_000_000), False),  # fund floor above target
        ((10_000_000, 20_000_000), (1_000_000, 5_000_000), False),  # fund ceiling below target
        ((4_000_000, 8_000_000), (None, 10_000_000), True),        # open-ended floor
        ((4_000_000, 8_000_000), (2_000_000, None), True),         # open-ended ceiling
    ],
)
def test_bands_overlap(target, fund, expected):
    assert _bands_overlap(*target, *fund) is expected


def test_bands_overlap_unknown_fund_band_is_kept():
    # Only about a third of firms publish a band at all. Treating "unpublished"
    # as a failed test would delete most of the universe before ranking.
    assert _bands_overlap(4_000_000, 8_000_000, None, None) is True


def test_bands_overlap_keeps_a_fund_whose_floor_sits_just_below_the_target_ceiling():
    assert _bands_overlap(1_000_000, 5_000_000, 5_000_000, 50_000_000) is True
    assert _bands_overlap(1_000_000, 5_000_000, 5_000_001, 50_000_000) is False


# --------------------------------------------------------------------------
# _naics_ok and the portfolio-aware NAICS universe
# --------------------------------------------------------------------------


def test_naics_ok_matches_at_the_two_digit_sector_level():
    # 238220 (plumbing/HVAC contractors) and 238160 (roofing) are different
    # six-digit codes in the same 23 sector, and should still meet.
    assert _naics_ok(["238220"], ["238160"]) is True
    assert _naics_ok(["238220"], ["541511"]) is False


@pytest.mark.parametrize("target,coverage", [([], ["541"]), (["238220"], []), ([], [])])
def test_naics_ok_unknown_coverage_is_not_a_rejection(target, coverage):
    assert _naics_ok(target, coverage) is True


def test_fund_naics_universe_includes_portfolio_codes():
    # The Alpine case: the firm markets itself as software and services
    # (51/54/56), so a fund-level NAICS test drops it for an HVAC target — even
    # though it owns Apex Service Partners, an HVAC roll-up, which makes it
    # precisely the right buyer. What a fund has bought outranks how it markets.
    alpine = make_fund(
        "Alpine Investors",
        naics_coverage=["511210", "541511", "561720"],
        platforms=[make_platform("Apex Service Partners", naics_codes=["238220"])],
    )
    universe = _fund_naics_universe(alpine)

    assert _naics_ok(["238220"], alpine.naics_coverage) is False  # fund-level test fails
    assert _naics_ok(["238220"], universe) is True                # portfolio rescues it


def test_fund_naics_universe_is_empty_without_coverage_or_platforms():
    assert _fund_naics_universe(make_fund(naics_coverage=[], platforms=[])) == []
