"""Held-out tests for issue #3: same contract, independent inputs and edges."""

import shutil
import subprocess

import yaml

from site_helpers import (
    REPO_ROOT, RESEARCH_ORDER, collapse_ws, page_text, redirects_to, resolve,
    run_publications_filter_js, soup_of,
)

PROJECTS_YML = REPO_ROOT / "data" / "projects.yml"
PUB_CATEGORIES_YML = REPO_ROOT / "data" / "publication_categories.yml"


def test_heldout_multicategory_publications_consistent_across_pages(site):
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"
    cat_map = yaml.safe_load(PUB_CATEGORIES_YML.read_text(encoding="utf8"))
    assert isinstance(cat_map, dict)

    multi_keys = [k for k, cats in cat_map.items() if len(cats) > 1]
    assert multi_keys, "expected at least one multi-category publication"

    # Build key sets rendered on each category page
    cat_page_keys = {}
    for slug in RESEARCH_ORDER:
        page = site / "research" / f"{slug}.html"
        assert page.is_file(), f"_site/research/{slug}.html missing"
        cat_page_keys[slug] = {
            a.get("data-key") for a in soup_of(page).select("article.pub-entry")
        }

    pub_page = site / "publications.html"
    pub_soup = soup_of(pub_page)
    pub_by_key = {a.get("data-key"): a for a in pub_soup.select("article.pub-entry")}

    for key in multi_keys:
        expected_canonical = [c for c in RESEARCH_ORDER if c in cat_map[key]]
        # Appears on every assigned category page and no unassigned category page
        for slug in RESEARCH_ORDER:
            if slug in expected_canonical:
                assert key in cat_page_keys[slug], f"{key} missing from research/{slug}.html"
            else:
                assert key not in cat_page_keys[slug], f"{key} unexpectedly present on research/{slug}.html"

        # Canonical order preserved in data-categories and .pub-category-badge links
        art = pub_by_key[key]
        assert (art.get("data-categories") or "").strip().split() == expected_canonical
        badges = art.select(".pub-categories a.pub-category-badge[href]")
        badge_targets = [resolve(site, pub_page, b["href"]).resolve() for b in badges]
        expected_targets = [(site / "research" / f"{s}.html").resolve() for s in expected_canonical]
        assert badge_targets == expected_targets


def test_heldout_client_side_filter_exact_token_matching_and_query_param(site):
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"
    cat_map = yaml.safe_load(PUB_CATEGORIES_YML.read_text(encoding="utf8"))

    page = site / "publications.html"

    # 1. Query param ?category=decision-making activates on load and hides empty sections
    qres = run_publications_filter_js(page, query="?category=decision-making")
    assert qres["found"], "no inline category filter <script> found in _site/publications.html"
    qinit = qres["initial"]
    expected_dm = {k for k, cats in cat_map.items() if "decision-making" in cats}
    visible_dm = {e["key"] for e in qinit["entries"] if e["visible"]}
    assert visible_dm == expected_dm
    dm_btn = next(b for b in qinit["buttons"] if b["category"] == "decision-making")
    assert dm_btn["ariaPressed"] == "true" and dm_btn["activeClass"]

    # 2. Exact whitespace-delimited token matching (not substring matching):
    # Inject synthetic entries with "adhd-sub" and "sub-adhd" and click "adhd"
    tok_res = run_publications_filter_js(
        page,
        query="",
        extra_entries=[
            {"key": "SynthPrefixToken", "categories": "adhd-sub", "title": "Prefix trap"},
            {"key": "SynthSuffixToken", "categories": "sub-adhd", "title": "Suffix trap"},
            {"key": "SynthExactMulti", "categories": "epi adhd", "title": "Exact multi"},
        ],
        actions=[{"type": "click", "category": "adhd", "label": "adhd"}],
    )
    assert tok_res["found"]
    by_key = {e["key"]: e["visible"] for e in tok_res["actions"]["adhd"]["entries"]}
    assert by_key["SynthPrefixToken"] is False, "filter matched 'adhd' as prefix substring of 'adhd-sub'"
    assert by_key["SynthSuffixToken"] is False, "filter matched 'adhd' as suffix substring of 'sub-adhd'"
    assert by_key["SynthExactMulti"] is True, "filter failed to match exact token 'adhd' in 'epi adhd'"

    # 3. Unknown query param does not crash and keeps all entries visible
    bad_qres = run_publications_filter_js(page, query="?category=nonexistent-slug")
    assert bad_qres["found"]
    assert all(e["visible"] for e in bad_qres["initial"]["entries"])


def test_heldout_same_year_project_tie_order_and_special_funder_rendering(tmp_path):
    assert PROJECTS_YML.is_file(), "data/projects.yml missing"
    proj_dir = tmp_path / "proj"
    shutil.copytree(
        REPO_ROOT,
        proj_dir,
        ignore=shutil.ignore_patterns("_site", ".quarto", ".git", ".pytest_cache", "__pycache__", ".issues"),
    )

    projects_path = proj_dir / "data" / "projects.yml"
    projects = yaml.safe_load(projects_path.read_text(encoding="utf8"))
    projects.extend([
        {
            "id": "tie-first-2028",
            "title": "First Same-Year Epidemiology Grant",
            "role": "PI",
            "funder": "FHI, HSØ & NFR",
            "year": 2028,
            "period": "2028–present",
            "status": "current",
            "categories": ["epi"],
            "description": "First declared same-year grant to verify stable tie ordering.",
            "papers": [],
        },
        {
            "id": "tie-second-2028",
            "title": "Second Same-Year Epidemiology Grant",
            "role": "co-PI",
            "funder": "NK AD/HD, Tourettes Syndrom og Narkolepsi",
            "year": 2028,
            "period": "2028–present",
            "status": "current",
            "categories": ["epi"],
            "description": "Second declared same-year grant to verify stable tie ordering.",
            "papers": [],
        },
    ])
    projects_path.write_text(yaml.safe_dump(projects, sort_keys=False, allow_unicode=True), encoding="utf8")

    quarto = shutil.which("quarto")
    assert quarto is not None
    proc = subprocess.run(
        [quarto, "render"], cwd=proj_dir, capture_output=True, text=True,
        encoding="utf8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]

    epi_soup = soup_of(proj_dir / "_site" / "research" / "epi.html")
    current_els = epi_soup.select('article.project-entry[data-status="current"]')
    ids = [e.get("data-project-id") for e in current_els]
    assert ids[:2] == ["tie-first-2028", "tie-second-2028"], (
        f"same-year ties did not preserve data/projects.yml declaration order: {ids!r}"
    )
    assert "FHI, HSØ & NFR" in collapse_ws(current_els[0].get_text(" "))
    assert "NK AD/HD, Tourettes Syndrom og Narkolepsi" in collapse_ws(current_els[1].get_text(" "))


def test_heldout_nfrsea_cross_link_and_decision_making_merged_content(site):
    assert PROJECTS_YML.is_file(), "data/projects.yml missing"
    projects = yaml.safe_load(PROJECTS_YML.read_text(encoding="utf8"))
    nfrsea_proj = next(p for p in projects if p["id"] == "nfrsea-2020")
    assert nfrsea_proj.get("url") == "nfrsea.html"
    assert nfrsea_proj["categories"], "nfrsea-2020 must belong to at least one category"

    nfrsea_target = (site / "research" / "nfrsea.html").resolve()
    assert nfrsea_target.is_file()

    for slug in nfrsea_proj["categories"]:
        page = site / "research" / f"{slug}.html"
        entry = soup_of(page).select_one('article.project-entry[data-project-id="nfrsea-2020"]')
        assert entry is not None, f"nfrsea-2020 missing from research/{slug}.html"
        assert any(
            resolve(site, page, a["href"]).resolve() == nfrsea_target
            for a in entry.select("a[href]")
        ), f"nfrsea-2020 on research/{slug}.html does not link to nfrsea.html"

    # Merged decision-making.html retains content from both dm and decneurosc/cogneuro
    dm_page = site / "research" / "decision-making.html"
    assert dm_page.is_file()
    dm_text = page_text(dm_page)
    assert "Max Planck Institute for Human Development" in dm_text
    assert "reinforcement learning" in dm_text
    assert "drift diffusion model" in dm_text

    # All 3 legacy decision/neuroscience project URLs redirect to decision-making.html
    for old_slug in ("dm", "decneurosc", "cogneuro"):
        old_page = site / "project" / old_slug / "index.html"
        assert old_page.is_file()
        assert redirects_to(site, old_page, dm_page)
