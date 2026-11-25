"""Held-out tests for issue #15: same contract, independent inputs and edge cases."""

import re
import shutil
import subprocess

import pytest
import yaml

from site_helpers import REPO_ROOT, collapse_ws, redirects_to, soup_of

RESEARCH_INTERESTS_YML = REPO_ROOT / "data" / "research_interests.yml"
PUB_CATEGORIES_YML = REPO_ROOT / "data" / "publication_categories.yml"

TOPIC_BY_INTEREST = {
    "causal-inference": ("causalinf", "causalinf.html"),
    "bayesian-statistics": ("bayes", "bayes.html"),
    "statistical-modelling": ("epi", "epi.html"),
    "adhd": ("adhd", "adhd.html"),
    "cognitive-modelling": ("decision-making", "decision-making.html"),
}

LEGACY_REDIRECTS = {
    "causalinf": "causalinf.html",
    "bayes": "bayes.html",
    "epi": "epi.html",
    "nfrsea": "nfrsea.html",
    "dm": "decision-making.html",
    "decneurosc": "decision-making.html",
    "cogneuro": "decision-making.html",
}


def _copy_project(dst):
    shutil.copytree(
        REPO_ROOT,
        dst,
        ignore=shutil.ignore_patterns("_site", ".quarto", ".git", ".pytest_cache", "__pycache__", ".issues"),
    )


def _run_quarto_render(project_dir):
    quarto = shutil.which("quarto")
    assert quarto is not None, "quarto executable not found on PATH"
    return subprocess.run(
        [quarto, "render"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        encoding="utf8",
        errors="replace",
        timeout=120,
    )


@pytest.mark.parametrize("mode", [
    "path_traversal_graphic",
    "non_positive_viewbox",
    "svg_onload_event_handler",
    "svg_javascript_uri",
])
def test_heldout_svg_validation_edge_cases(tmp_path, mode):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / f"proj_{mode}"
    _copy_project(proj_dir)

    yml_path = proj_dir / "data" / "research_interests.yml"
    interests = yaml.safe_load(yml_path.read_text(encoding="utf8"))

    if mode == "path_traversal_graphic":
        outside = tmp_path / "outside.svg"
        outside.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<rect x="1" y="1" width="10" height="10"/></svg>',
            encoding="utf8",
        )
        interests[1]["graphic"] = "../outside.svg"
    elif mode == "non_positive_viewbox":
        bad_svg = proj_dir / "research" / "graphics" / "bad_vb.svg"
        bad_svg.parent.mkdir(parents=True, exist_ok=True)
        bad_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 0 100">'
            '<rect x="1" y="1" width="10" height="10"/></svg>',
            encoding="utf8",
        )
        interests[2]["graphic"] = "research/graphics/bad_vb.svg"
    elif mode == "svg_onload_event_handler":
        bad_svg = proj_dir / "research" / "graphics" / "onload.svg"
        bad_svg.parent.mkdir(parents=True, exist_ok=True)
        bad_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" onload="alert(1)">'
            '<rect x="1" y="1" width="10" height="10"/></svg>',
            encoding="utf8",
        )
        interests[3]["graphic"] = "research/graphics/onload.svg"
    elif mode == "svg_javascript_uri":
        bad_svg = proj_dir / "research" / "graphics" / "jsuri.svg"
        bad_svg.parent.mkdir(parents=True, exist_ok=True)
        bad_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<a href="javascript:alert(1)"><rect x="1" y="1" width="10" height="10"/></a></svg>',
            encoding="utf8",
        )
        interests[4]["graphic"] = "research/graphics/jsuri.svg"

    yml_path.write_text(yaml.safe_dump(interests, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode != 0, (
        f"quarto render unexpectedly succeeded for held-out SVG validation mode {mode!r}"
    )


def test_heldout_unique_dom_ids_and_svg_internal_refs(site):
    index_soup = soup_of(site / "research" / "index.html")
    cards = index_soup.select("main .research-cards-grid article.research-card")
    assert len(cards) == 5

    body = index_soup.body
    assert body is not None, "research/index.html missing <body>"
    all_ids = [el.get("id") for el in body.find_all(id=True) if el.get("id")]
    id_set = set(all_ids)
    assert len(all_ids) == len(id_set), (
        f"duplicate id values in research/index.html <body>: "
        f"{sorted({i for i in all_ids if all_ids.count(i) > 1})!r}"
    )

    # Every internal url(#...) reference inside inline SVGs must resolve to a valid id in <body>
    for card in cards:
        svg = card.select_one(".research-card-graphic svg")
        assert svg is not None
        svg_str = str(svg)
        for ref_id in re.findall(r"url\(\s*['\"]?#([A-Za-z0-9_.:-]+)['\"]?\s*\)", svg_str):
            assert ref_id in id_set, (
                f"dangling SVG url(#{ref_id}) reference in card {card.get('data-interest-id')!r}"
            )

    # No data-* attribute anywhere in research/index.html leaks raw <svg markup
    for el in index_soup.find_all(True):
        for attr_name, attr_val in el.attrs.items():
            if attr_name.startswith("data-"):
                val = " ".join(attr_val) if isinstance(attr_val, list) else str(attr_val)
                assert "<svg" not in val.casefold(), (
                    f"raw <svg markup leaked into {attr_name!r} on <{el.name}>"
                )


def test_heldout_dynamic_rebuild_editing_third_svg_updates_only_its_card(tmp_path):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / "proj_svg_edit_third"
    _copy_project(proj_dir)

    interests = yaml.safe_load((proj_dir / "data" / "research_interests.yml").read_text(encoding="utf8"))
    target_interest = interests[2]
    target_id = target_interest["id"]
    graphic_rel = target_interest.get("graphic")
    assert isinstance(graphic_rel, str) and graphic_rel.strip(), f"{target_id!r} missing graphic field"

    svg_path = proj_dir / graphic_rel
    assert svg_path.is_file(), f"graphic file missing: {svg_path}"
    orig_svg = svg_path.read_text(encoding="utf8")
    marker = "Heldout Statistical Modelling SVG Marker"
    assert "</svg>" in orig_svg
    svg_path.write_text(
        orig_svg.replace("</svg>", f'<text x="5" y="15">{marker}</text></svg>', 1),
        encoding="utf8",
    )

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode == 0, f"quarto render failed:\n{proc.stderr[-2000:]}"

    index_soup = soup_of(proj_dir / "_site" / "research" / "index.html")
    cards = index_soup.select("main .research-cards-grid article.research-card")
    assert len(cards) == 5

    for card in cards:
        cid = card.get("data-interest-id")
        svg = card.select_one(".research-card-graphic svg")
        assert svg is not None
        svg_text = collapse_ws(svg.get_text(" "))
        if cid == target_id:
            assert marker in svg_text, f"edited SVG marker missing from {target_id!r} card"
        else:
            assert marker not in svg_text, f"edited SVG marker leaked into unrelated card {cid!r}"

    # Linked topic page overview remains intact
    epi_soup = soup_of(proj_dir / "_site" / "research" / "epi.html")
    assert epi_soup.select_one(f'main section.research-interest[data-interest-id="{target_id}"]') is not None


def test_heldout_topic_pages_preserve_pub_counts_and_legacy_redirects(site):
    assert RESEARCH_INTERESTS_YML.is_file()
    assert PUB_CATEGORIES_YML.is_file()

    interests = yaml.safe_load(RESEARCH_INTERESTS_YML.read_text(encoding="utf8"))
    by_id = {entry["id"]: entry for entry in interests}
    cat_map = yaml.safe_load(PUB_CATEGORIES_YML.read_text(encoding="utf8"))

    for interest_id, (cat_slug, html_name) in TOPIC_BY_INTEREST.items():
        page = site / "research" / html_name
        soup = soup_of(page)

        # Highlighted papers on topic page do not alter article.pub-entry count
        expected_pub_keys = {k for k, cats in cat_map.items() if cat_slug in cats}
        rendered_pub_keys = [a.get("data-key") for a in soup.select("article.pub-entry")]
        assert set(rendered_pub_keys) == expected_pub_keys and len(rendered_pub_keys) == len(expected_pub_keys), (
            f"article.pub-entry count/set changed on research/{html_name}"
        )

        sec = soup.select_one(f'main section.research-interest[data-interest-id="{interest_id}"]')
        assert sec is not None, f"missing section.research-interest on research/{html_name}"
        hl_keys = [li.get("data-key") for li in sec.select("ul.highlighted-papers-list li.highlighted-paper")]
        assert hl_keys == by_id[interest_id]["papers"], (
            f"highlighted papers on research/{html_name} ({hl_keys!r}) != yml ({by_id[interest_id]['papers']!r})"
        )

    for slug, target in LEGACY_REDIRECTS.items():
        redirect_page = site / "project" / slug / "index.html"
        expected_page = site / "research" / target
        assert redirect_page.is_file(), f"missing legacy redirect _site/project/{slug}/index.html"
        assert redirects_to(site, redirect_page, expected_page), (
            f"legacy redirect _site/project/{slug}/index.html no longer redirects to research/{target}"
        )
