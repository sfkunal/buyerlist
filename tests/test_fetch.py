"""Page selection and the model-facing document.

Everything here runs offline: `score_url`, `_cap_by_path_prefix`, `_squeeze`,
`_looks_like_error_page`, `discover_links` and `extract_text` are pure over
strings, and `SiteSnapshot.to_prompt_text` is pure over already-fetched pages.

This is the layer that decides what the model reads, so its failures look like
a worse answer rather than an error — the reason it is worth pinning.
"""

from __future__ import annotations

import re

import pytest

from buyerlist.fetch import (
    FetchedPage,
    SiteSnapshot,
    _cap_by_path_prefix,
    _looks_like_error_page,
    _squeeze,
    discover_links,
    extract_text,
    score_url,
)


def page(url: str, text: str, score: int, title: str = "t") -> FetchedPage:
    return FetchedPage(url=url, title=title, text=text, score=score, status=200)


def bodies(rendered: str) -> list[str]:
    """The page bodies out of `to_prompt_text`, without the <page> wrappers.

    Budget assertions have to measure the text the model reads, not the markup
    around it — counting a character across the whole render also counts the
    URL and title attributes, which silently inflates the total.
    """
    return re.findall(r"<page [^>]*>\n(.*?)\n</page>", rendered, flags=re.S)


# --------------------------------------------------------------------------
# score_url
# --------------------------------------------------------------------------


def test_score_url_ranks_the_pages_that_carry_signal_highest():
    # The ordering matters more than the absolute numbers: these are what the
    # crawler spends its twelve-page budget on.
    assert score_url("https://x.com/about", "About", "company") == 12
    assert score_url("https://x.com/services", "Services", "company") == 11
    assert score_url("https://x.com/blog/post", "Read more", "company") == 0


def test_score_url_fund_profile_puts_criteria_and_portfolio_on_top():
    criteria = score_url("https://f.com/investment-criteria", "Criteria", "fund")
    portfolio = score_url("https://f.com/portfolio", "Portfolio", "fund")
    team = score_url("https://f.com/team", "Team", "fund")

    assert criteria > portfolio > team
    # The criteria page is the single highest-value page for a PE firm, so it
    # must outrank everything else the fund profile knows about.
    assert criteria == 17


@pytest.mark.parametrize(
    "url,profile",
    [
        ("https://x.com/privacy-policy", "company"),
        ("https://x.com/cart", "company"),
        ("https://x.com/wp-content/uploads/a.pdf", "company"),
        ("https://x.com/tag/hvac/", "company"),
        ("https://f.com/legal", "fund"),
        ("https://f.com/disclosure", "fund"),
    ],
)
def test_score_url_skip_list_is_a_hard_reject(url, profile):
    assert score_url(url, "", profile) == -1


def test_score_url_skip_list_also_matches_offsite_social_links():
    # The skip terms are tested against the full URL as well as the path, so a
    # link whose path looks harmless is still rejected on its host.
    assert score_url("https://www.facebook.com/company", "Follow us", "company") == -1


def test_score_url_rewards_shallow_paths_and_penalises_deep_ones():
    # Same keyword, different depth: section pages beat leaf content.
    shallow = score_url("https://x.com/team", "Team", "company")
    deep = score_url("https://x.com/a/b/c/team", "Team", "company")
    assert shallow == deep + 5  # +2 for depth<=1 against -3 for depth>=4


def test_score_url_unkeyworded_deep_page_scores_negative_and_is_never_fetched():
    # discover_links only keeps scores > 0, so this is the mechanism that keeps
    # deep leaf content out of the budget entirely.
    assert score_url("https://x.com/a/b/c/d/deep", "x", "company") == -3


# --------------------------------------------------------------------------
# _cap_by_path_prefix
# --------------------------------------------------------------------------


def test_cap_by_path_prefix_stops_doorway_pages_crowding_out_real_ones():
    # SMB sites publish a templated page per suburb — /service-area/affton,
    # /service-area/arnold, /service-area/ballwin — which all score identically
    # and carry near-identical text. Without the cap they fill the budget.
    ranked = [
        ("https://x.com/service-area/affton", 9),
        ("https://x.com/service-area/arnold", 9),
        ("https://x.com/service-area/ballwin", 9),
        ("https://x.com/service-area/clayton", 9),
        ("https://x.com/about", 12),
        ("https://x.com/team", 9),
    ]
    kept = _cap_by_path_prefix(ranked, limit=5)

    suburbs = [u for u, _ in kept if "/service-area/" in u]
    assert len(suburbs) == 2
    assert "https://x.com/about" in dict(kept)
    assert "https://x.com/team" in dict(kept)


def test_cap_by_path_prefix_honours_the_limit():
    ranked = [(f"https://x.com/p{i}", 5) for i in range(10)]
    assert len(_cap_by_path_prefix(ranked, limit=4)) == 4


def test_cap_by_path_prefix_preserves_input_order():
    # Input arrives score-sorted; the cap must not reshuffle it, because the
    # order is what truncation later depends on.
    ranked = [("https://x.com/about", 12), ("https://x.com/services", 11)]
    assert _cap_by_path_prefix(ranked, limit=5) == ranked


def test_cap_by_path_prefix_gives_each_top_level_page_its_own_bucket():
    # Single-segment pages must not share a prefix bucket, or /about would
    # squeeze out /team.
    ranked = [(f"https://x.com/{s}", 9) for s in ("about", "team", "services", "contact")]
    assert len(_cap_by_path_prefix(ranked, limit=10)) == 4


def test_cap_by_path_prefix_handles_an_empty_ranking():
    assert _cap_by_path_prefix([], limit=5) == []


# --------------------------------------------------------------------------
# Text cleanup
# --------------------------------------------------------------------------


def test_squeeze_collapses_runs_of_blank_lines_and_spaces():
    assert _squeeze("a\n\n\n\n\nb") == "a\n\nb"
    assert _squeeze("a      b") == "a b"


def test_squeeze_caps_length():
    assert len(_squeeze("x" * 50_000)) == 12_000
    assert len(_squeeze("x" * 50_000, limit=100)) == 100


def test_squeeze_strips_surrounding_whitespace():
    assert _squeeze("\n\n  hello  \n\n") == "hello"


# --------------------------------------------------------------------------
# _looks_like_error_page
# --------------------------------------------------------------------------


def test_looks_like_error_page_catches_a_themed_404_served_as_200():
    # These carry enough chrome to clear the 120-char minimum, so without this
    # check they reach the model as though they were the About page.
    title = "Page Not Found — Acme Plumbing"
    text = "Nothing Found. The post you are looking for is not available. Try a search instead."
    assert _looks_like_error_page(title, text) is True


def test_looks_like_error_page_leaves_a_real_page_alone():
    assert _looks_like_error_page("About Us", "Acme Plumbing has served the metro since 1950.") is False


def test_looks_like_error_page_ignores_long_pages_that_merely_mention_404():
    # A substantial page that happens to discuss error handling is real content.
    text = "We build web software. " * 100 + " Our guide covers the 404 not found response."
    assert len(text) > 1500
    assert _looks_like_error_page("Engineering blog", text) is False


def test_looks_like_error_page_only_examines_the_opening():
    # The marker sits past the 400-character window, so this is not an error page.
    text = "Real content about our services. " * 20 + "page not found"
    assert _looks_like_error_page("Services", text) is False


# --------------------------------------------------------------------------
# discover_links
# --------------------------------------------------------------------------


HTML = """
<html><head><title>Acme Plumbing</title></head><body>
  <nav>
    <a href="/about">About Us</a>
    <a href="/services">Services</a>
    <a href="/privacy-policy">Privacy</a>
    <a href="https://www.facebook.com/acme">Facebook</a>
    <a href="#main">Skip to content</a>
    <a href="mailto:hi@acme.com">Email</a>
    <a href="tel:+15551234567">Call</a>
    <a href="https://othersite.com/about">Partner</a>
    <a href="/about/">About again</a>
  </nav>
  <main><p>We have served the metro area since 1950 with plumbing and HVAC work.</p></main>
</body></html>
"""


def test_discover_links_keeps_only_same_host_scoring_links():
    found = dict(discover_links(HTML, "https://www.acme.com", "company"))
    assert "https://www.acme.com/about" in found
    assert "https://www.acme.com/services" in found
    # Rejected: skip-listed, offsite, and non-navigational schemes.
    assert not any("privacy" in u for u in found)
    assert not any("facebook" in u for u in found)
    assert not any("othersite" in u for u in found)
    assert not any(u.startswith(("mailto:", "tel:")) for u in found)


def test_discover_links_normalises_trailing_slashes_and_fragments():
    # "/about" and "/about/" are one page; counting them twice would spend two
    # slots of a twelve-page budget on the same text.
    found = dict(discover_links(HTML, "https://www.acme.com", "company"))
    assert len([u for u in found if u.rstrip("/").endswith("/about")]) == 1


def test_discover_links_returns_score_descending():
    scores = [s for _, s in discover_links(HTML, "https://www.acme.com", "company")]
    assert scores == sorted(scores, reverse=True)


def test_discover_links_treats_www_and_bare_host_as_the_same_site():
    found = dict(discover_links(HTML, "https://acme.com", "company"))
    assert "https://acme.com/about" in found


def test_discover_links_survives_unparseable_markup():
    assert discover_links("<<<not html", "https://acme.com", "company") == []


# --------------------------------------------------------------------------
# extract_text
# --------------------------------------------------------------------------


def test_extract_text_returns_title_and_body():
    title, text = extract_text(HTML)
    assert title == "Acme Plumbing"
    assert "served the metro area since 1950" in text


def test_extract_text_falls_back_to_dom_text_when_the_article_parser_bails():
    # trafilatura is tuned for articles and returns nothing on the brochure
    # markup that dominates this dataset, so the DOM fallback is load-bearing.
    brochure = (
        "<html><head><title>Acme</title></head><body>"
        "<script>var a=1;</script><nav>Home Contact</nav>"
        + "<div>Quality service for the metro area. </div>" * 20
        + "<footer>© Acme</footer></body></html>"
    )
    title, text = extract_text(brochure)
    assert title == "Acme"
    assert "Quality service for the metro area." in text
    # Script, nav and footer chrome is stripped by the fallback path.
    assert "var a=1" not in text


def test_extract_text_on_empty_input_returns_empty_strings():
    assert extract_text("") == ("", "")


# --------------------------------------------------------------------------
# SiteSnapshot — the model-facing document and its budget
# --------------------------------------------------------------------------


def test_total_chars_sums_page_lengths():
    snap = SiteSnapshot(
        root_url="https://x.com",
        profile="company",
        pages=[page("https://x.com/a", "x" * 100, 12), page("https://x.com/b", "y" * 50, 9)],
    )
    assert snap.pages[0].chars == 100
    assert snap.total_chars == 150


def test_to_prompt_text_wraps_each_page_with_its_url_and_title():
    snap = SiteSnapshot(
        root_url="https://x.com",
        profile="fund",
        pages=[page("https://x.com/criteria", "EBITDA of $3M+", 15, title="Criteria")],
    )
    out = snap.to_prompt_text()
    assert '<page url="https://x.com/criteria" title="Criteria">' in out
    assert "EBITDA of $3M+" in out
    assert out.endswith("</page>")


def test_to_prompt_text_caps_each_page_by_its_score_tier():
    # High-scoring criteria and portfolio pages carry the signal; news and
    # listing pages are mostly repeated boilerplate and get a smaller slice.
    high = page("https://x.com/criteria", "a" * 20_000, 15)
    mid = page("https://x.com/about", "b" * 20_000, 9)
    low = page("https://x.com/news", "c" * 20_000, 4)
    snap = SiteSnapshot(root_url="https://x.com", profile="fund", pages=[high, mid, low])

    got = [len(b) for b in bodies(snap.to_prompt_text(max_chars=200_000))]
    assert got == [12_000, 6_000, 2_500]


def test_to_prompt_text_truncation_drops_the_least_useful_page_not_every_tail():
    # Pages arrive score-ordered, so spending the budget in order means the page
    # that gets cut is the one that mattered least.
    first = page("https://x.com/criteria", "a" * 8_000, 15)
    second = page("https://x.com/news", "c" * 8_000, 4)
    snap = SiteSnapshot(root_url="https://x.com", profile="fund", pages=[first, second])

    out = snap.to_prompt_text(max_chars=8_000)
    assert [len(b) for b in bodies(out)] == [8_000]  # the important page survives whole
    assert "https://x.com/news" not in out  # the budget ran out before it


def test_to_prompt_text_never_exceeds_its_budget_in_body_text():
    pages = [page(f"https://x.com/{i}", "z" * 5_000, 15) for i in range(10)]
    snap = SiteSnapshot(root_url="https://x.com", profile="fund", pages=pages)
    assert sum(len(b) for b in bodies(snap.to_prompt_text(max_chars=12_000))) == 12_000


def test_to_prompt_text_on_an_empty_snapshot_is_empty():
    assert SiteSnapshot(root_url="https://x.com", profile="company").to_prompt_text() == ""
