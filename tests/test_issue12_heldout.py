"""Held-out tests for issue #12: same contract, independent inputs and edges."""

import re
import shutil
import subprocess

import pytest
import yaml

from site_helpers import REPO_ROOT, collapse_ws, is_internal, resolve, soup_of

RESEARCH_INTERESTS_YML = REPO_ROOT / "data" / "research_interests.yml"
CANONICAL_CATEGORY_HTML = {
    "causalinf.html", "bayes.html", "adhd.html", "epi.html", "decision-making.html",
}
PAPER_LINK_LABELS = {"DOI", "PDF", "Preprint", "Proceedings", "Report", "URL"}


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


def _replace_link_target(item, new_target):
    if isinstance(item, str):
        return new_target
    if isinstance(item, dict):
        updated = dict(item)
        for key in ("href", "url", "target", "page"):
            if key in updated and isinstance(updated[key], str):
                updated[key] = new_target
                return updated
        updated["href"] = new_target
        return updated
    return new_target


def test_heldout_dynamic_rebuild_editing_existing_highlighted_paper_in_bib(tmp_path):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / "proj_edit_bib"
    _copy_project(proj_dir)

    interests = yaml.safe_load((proj_dir / "data" / "research_interests.yml").read_text(encoding="utf8"))
    target_key = interests[1]["papers"][0]
    target_section_id = interests[1]["id"]

    bib_path = proj_dir / "data" / "references.bib"
    bib_text = bib_path.read_text(encoding="utf8")

    # Locate the BibTeX entry for target_key and update its title and doi in place
    entry_start = re.search(rf"@\w+\s*\{{\s*{re.escape(target_key)}\s*,", bib_text)
    assert entry_start is not None, f"could not locate {target_key!r} in data/references.bib"
    next_entry = re.search(r"\n@\w+\s*\{", bib_text[entry_start.end():])
    entry_end = entry_start.end() + next_entry.start() if next_entry else len(bib_text)
    block = bib_text[entry_start.start():entry_end]

    new_title = "Updated Heldout Highlighted Paper Title 2027"
    new_doi = "10.5555/heldout-updated-doi-2027"

    block_new, n_title = re.subn(
        r"(title\s*=\s*)\{\{.*?\}\}|(title\s*=\s*)\{.*?\}",
        rf"\g<1>\g<2>{{{{{new_title}}}}}",
        block,
        count=1,
        flags=re.S | re.I,
    )
    assert n_title == 1, f"failed to replace title in {target_key}"

    if re.search(r"\bdoi\s*=", block_new, flags=re.I):
        block_new, n_doi = re.subn(
            r"(\bdoi\s*=\s*)\{[^}]*\}",
            rf"\g<1>{{{new_doi}}}",
            block_new,
            count=1,
            flags=re.I,
        )
        assert n_doi == 1
    else:
        block_new = block_new.rstrip()
        assert block_new.endswith("}")
        block_new = block_new[:-1].rstrip().rstrip(",") + f",\n  doi = {{{new_doi}}}\n}}"

    bib_path.write_text(bib_text[:entry_start.start()] + block_new + bib_text[entry_end:], encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode == 0, f"quarto render failed:\n{proc.stderr[-2000:]}"

    soup = soup_of(proj_dir / "_site" / "research" / "index.html")
    item = soup.select_one(
        f'section.research-interest[data-interest-id="{target_section_id}"] '
        f'li.highlighted-paper[data-key="{target_key}"]'
    )
    assert item is not None, f"highlighted paper {target_key!r} missing after bib edit"
    assert new_title in collapse_ws(item.get_text(" "))
    assert any(
        collapse_ws(a.get_text(" ")) == "DOI"
        and a["href"].strip().casefold() == f"https://doi.org/{new_doi}"
        for a in item.select("a.pub-link[href]")
    ), f"updated DOI {new_doi!r} not propagated to research/index.html"


def test_heldout_local_pdf_link_resolves_from_research_index(tmp_path):
    """Biele2019-fd has a local `pdf = {files/Biele_2019.pdf}` field; its PDF link on
    research/index.html (a subdirectory page) must resolve to _site/files/Biele_2019.pdf."""
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / "proj_local_pdf"
    _copy_project(proj_dir)

    yml_path = proj_dir / "data" / "research_interests.yml"
    interests = yaml.safe_load(yml_path.read_text(encoding="utf8"))
    if not any("Biele2019-fd" in sec["papers"] for sec in interests):
        interests[0]["papers"] = ["Biele2019-fd"] + interests[0]["papers"][1:]
        yml_path.write_text(yaml.safe_dump(interests, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode == 0, f"quarto render failed:\n{proc.stderr[-2000:]}"

    out_site = proj_dir / "_site"
    page = out_site / "research" / "index.html"
    soup = soup_of(page)
    item = soup.select_one('li.highlighted-paper[data-key="Biele2019-fd"]')
    assert item is not None, "Biele2019-fd missing from rendered research/index.html"

    pdf_links = [
        a for a in item.select("a.pub-link[href]")
        if collapse_ws(a.get_text(" ")) == "PDF"
    ]
    assert pdf_links, "Biele2019-fd missing PDF a.pub-link on research/index.html"
    href = pdf_links[0]["href"].strip()
    assert is_internal(href), f"expected internal local PDF link, got {href!r}"
    resolved = resolve(out_site, page, href).resolve()
    assert resolved == (out_site / "files" / "Biele_2019.pdf").resolve(), (
        f"local PDF link {href!r} on research/index.html resolved to {resolved}, "
        f"expected {(out_site / 'files' / 'Biele_2019.pdf').resolve()}"
    )
    assert resolved.is_file(), f"resolved local PDF does not exist: {resolved}"


@pytest.mark.parametrize("mode", [
    "nfrsea_link_target",
    "whitespace_description",
    "zero_papers",
    "missing_title",
])
def test_heldout_validation_edge_cases(tmp_path, mode):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / f"proj_{mode}"
    _copy_project(proj_dir)

    yml_path = proj_dir / "data" / "research_interests.yml"
    interests = yaml.safe_load(yml_path.read_text(encoding="utf8"))

    if mode == "nfrsea_link_target":
        # nfrsea.html exists in research/, but is NOT one of the 5 canonical category pages
        interests[3]["links"][0] = _replace_link_target(interests[3]["links"][0], "nfrsea.html")
    elif mode == "whitespace_description":
        interests[2]["description"] = "   \n\t  "
    elif mode == "zero_papers":
        interests[4]["papers"] = []
    elif mode == "missing_title":
        del interests[1]["title"]

    yml_path.write_text(yaml.safe_dump(interests, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode != 0, (
        f"quarto render unexpectedly succeeded for held-out invalid mode {mode!r}"
    )


def test_heldout_research_interest_more_links_cover_all_five_categories_bijectively(site):
    page = site / "research" / "index.html"
    soup = soup_of(page)
    sections = soup.select("main section.research-interest")
    assert len(sections) == 5

    covered_targets = []
    for sec in sections:
        more = sec.select_one("div.research-interest-more")
        assert more is not None
        internal_resolved = [
            resolve(site, page, a["href"]).resolve()
            for a in more.find_all("a", href=True)
            if is_internal(a["href"])
        ]
        assert all(p.is_file() for p in internal_resolved), (
            f"broken internal link in div.research-interest-more: {internal_resolved!r}"
        )
        cat_targets = [
            p.name for p in internal_resolved
            if p.parent == (site / "research").resolve() and p.name in CANONICAL_CATEGORY_HTML
        ]
        assert len(cat_targets) == 1, (
            f"expected 1 canonical category link in div.research-interest-more, got {cat_targets!r}"
        )
        covered_targets.append(cat_targets[0])

        for item in sec.select("ul.highlighted-papers-list li.highlighted-paper"):
            links = item.select("a.pub-link[href]")
            assert links, f"no a.pub-link in {item.get('data-key')!r}"
            for a in links:
                href = a["href"].strip()
                assert href and href != "#" and not href.lower().startswith("javascript:"), (
                    f"invalid href {href!r} in {item.get('data-key')!r}"
                )
            labels = {collapse_ws(a.get_text(" ")) for a in links}
            assert labels & PAPER_LINK_LABELS, (
                f"paper {item.get('data-key')!r} only has non-paper links {labels!r}"
            )

    assert set(covered_targets) == CANONICAL_CATEGORY_HTML
    assert len(covered_targets) == len(set(covered_targets))
