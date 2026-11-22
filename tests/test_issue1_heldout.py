"""Held-out checks for issue #1 (same contract, different inputs/angles)."""

import re

import pytest

from site_helpers import (
    REPO_ROOT, RESEARCH_SLUGS, norm, page_text, parse_bib, perfect_matching,
    redirects_to, soup_of,
)

# A second plain-text sentence per category page carrying legacy prose
# (different from the visible tests), plus a topic check for the new ADHD page.
OTHER_SENTENCES = {
    "causalinf": "Our approach is to reduce this imperfection as much as we can, and to be "
                 "transparent about the remaining uncertainties like unobserved confounders "
                 "when we describe results.",
    "bayes": "So even if Bayesian estimation of large epidemiological data sets can be much "
             "slower, I still prefer to wait for results I think I can trust more.",
    "adhd": "ADHD",
    "epi": "The reason is that participation predictors are often colliders between (mental "
           "health) outcomes and exposures, and conditioning on colliders introduces bias, but "
           "weighting does not.",
    "decision-making": "This research line, in which I am currently not active, used experiments "
                       "and mathematical modeling to understand decision making in a social context.",
}


@pytest.mark.parametrize("slug", RESEARCH_SLUGS)
def test_research_page_contains_second_old_sentence(site, slug):
    page = site / "research" / f"{slug}.html"
    assert page.is_file()
    assert OTHER_SENTENCES[slug] in page_text(page)


def test_publication_entries_match_old_titles_one_to_one(site, legacy):
    page = site / "publications.html"
    assert page.is_file()
    entries = [norm(d.get_text(" ")) for d in soup_of(page).select("article.pub-entry .pub-title")]
    titles = [norm(p["title"]) for p in legacy["publications"]]
    assert len(entries) == 69
    assert len(titles) == 44
    matched = perfect_matching(titles, entries, lambda t, e: t in e)
    assert matched == 44, f"only {matched} of 44 titles can be paired with distinct entries"


@pytest.mark.parametrize("pub_id", ["pub_2007a", "pub_2016c", "pub_2021e"])
def test_old_publication_url_lands_on_page_listing_it(site, legacy, pub_id):
    record = next(p for p in legacy["publications"] if p["pub_id"] == pub_id)
    old = site / "publication" / pub_id / "index.html"
    target = site / "publications.html"
    assert old.is_file()
    assert redirects_to(site, old, target)
    entries = [norm(d.get_text(" ")) for d in soup_of(target).select("article.pub-entry .pub-title")]
    assert any(norm(record["title"]) in e for e in entries)


def test_legacy_bib_has_one_entry_per_old_publication(legacy):
    path = REPO_ROOT / "data" / "references.bib"
    assert path.is_file(), "data/references.bib missing"
    bib_titles = [
        norm(re.sub(r"\\[A-Za-z]+\*?", "", e["fields"].get("title", "")))
        for e in parse_bib(path.read_text(encoding="utf8"))
    ]
    for p in legacy["publications"]:
        assert bib_titles.count(norm(p["title"])) == 1, p["pub_id"]
