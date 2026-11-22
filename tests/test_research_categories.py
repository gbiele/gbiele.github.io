"""Visible tests for issue #3: research categories, CV projects, and publication filtering."""

import re
import shutil
import subprocess
import unicodedata

import pytest
import yaml

from site_helpers import (
    REPO_ROOT, RESEARCH_ORDER, collapse_ws, internal_urls, norm, page_text,
    redirects_to, resolve, soup_of,
)

CANONICAL_CATEGORIES = ["causalinf", "bayes", "adhd", "epi", "decision-making"]
CATEGORY_TITLES = {
    "causalinf": "Causal inference",
    "bayes": "Bayesian statistics",
    "adhd": "ADHD",
    "epi": "Epidemiology",
    "decision-making": "Decision making & neuroscience",
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

PROJECTS_YML = REPO_ROOT / "data" / "projects.yml"
PUB_CATEGORIES_YML = REPO_ROOT / "data" / "publication_categories.yml"
REFERENCES_BIB = REPO_ROOT / "data" / "references.bib"

FORBIDDEN_GRANT_AMOUNTS = [
    "25 000k", "12 000k", "10 000k", "8 400k", "7 000k",
    "6 000k", "2 250k", "1 200k", "300k", "250k", "k NOK",
]
FORBIDDEN_PROJECT_CURRENCY_RE = re.compile(r"\b(?:NOK|EUR|USD|million)\b|mill\.", re.I)

EXPLICIT_CV_FUNDERS = {
    "NFR",
    "HSØ",
    "NK AD/HD, Tourettes Syndrom og Narkolepsi",
    "BMBF",
}

PUBLISHED_TYPES = {"article", "inproceedings", "techreport"}
PREPRINT_TYPES = {"misc", "unpublished"}


# --- Independent helpers (never import scripts/) ---------------------------


def _read_balanced(text, i, open_ch, close_ch):
    depth = 1
    j = i
    while j < len(text) and depth:
        ch = text[j]
        if ch == "\\" and j + 1 < len(text):
            j += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
        j += 1
    return text[i:j - 1], j


def _parse_bib_entries(path=REFERENCES_BIB):
    assert path.is_file(), f"{path} missing"
    text = path.read_text(encoding="utf8")
    entries = []
    entry_re = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
    pos = 0
    while pos < len(text):
        m = entry_re.search(text, pos)
        if not m:
            break
        kind = m.group(1).lower()
        open_ch = m.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        body, end_pos = _read_balanced(text, m.end(), open_ch, close_ch)
        pos = end_pos
        if kind in ("comment", "string", "preamble"):
            continue
        k = body.find(",")
        key = body[:k].strip() if k >= 0 else body.strip()
        rest = body[k + 1:] if k >= 0 else ""
        fields = {}
        fpos = 0
        fm = re.compile(r"\s*([A-Za-z_\-]+)\s*=\s*")
        while fpos < len(rest):
            mm = fm.match(rest, fpos)
            if not mm:
                fpos += 1
                continue
            name = mm.group(1).lower()
            p = mm.end()
            if p < len(rest) and rest[p] == "{":
                val, p = _read_balanced(rest, p + 1, "{", "}")
            elif p < len(rest) and rest[p] == '"':
                q = p + 1
                depth = 0
                while q < len(rest) and not (rest[q] == '"' and depth == 0):
                    if rest[q] == "\\" and q + 1 < len(rest):
                        q += 2
                        continue
                    depth += {"{": 1, "}": -1}.get(rest[q], 0)
                    q += 1
                val, p = rest[p + 1:q], q + 1
            else:
                vm = re.match(r"[^,]*", rest[p:])
                val, p = vm.group(0).strip(), p + vm.end()
            fields[name] = val.strip()
            nxt = rest.find(",", p)
            fpos = len(rest) if nxt < 0 else nxt + 1
        entries.append({"key": key, "type": kind, "fields": fields})
    return entries


def _first_author_family(author_field):
    first = author_field.split(" and ")[0].strip()
    first = re.sub(r"[{}]", "", first)
    if "," in first:
        family = first.split(",", 1)[0].strip()
    else:
        family = first
    tokens = [t for t in family.split() if t]
    return tokens[-1] if tokens else ""


def _strip_accents_norm(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return norm(text)


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


# --- 1. Category Pages, Index Order, Cleanup, Redirects --------------------


def test_research_index_lists_five_canonical_categories_in_order(site):
    page = site / "research" / "index.html"
    assert page.is_file()
    soup = soup_of(page)
    main = soup.find("main") or soup.body or soup

    listing = soup.select_one("#listing-listing")
    assert listing is not None, "missing #listing-listing on research/index.html"
    cards = listing.select(".quarto-grid-item, .listing-item, .card")
    # Collect unique research/*.html targets linked from listing cards in order
    wanted = {(site / "research" / f"{s}.html").resolve(): s for s in CANONICAL_CATEGORIES}
    nfrsea_target = (site / "research" / "nfrsea.html").resolve()
    seen = []
    for a in main.find_all("a", href=True):
        href = a["href"]
        if not href.strip() or href.startswith("#") or re.match(r"^[a-zA-Z][\w+.\-]*:", href):
            continue
        resolved = resolve(site, page, href).resolve()
        assert resolved != nfrsea_target, "nfrsea.html must not be listed as a top-level card on research/index.html"
        slug = wanted.get(resolved)
        if slug and slug not in seen:
            seen.append(slug)

    assert seen == CANONICAL_CATEGORIES, f"expected category order {CANONICAL_CATEGORIES}, got {seen}"
    descs = listing.select(".listing-description")
    assert len(descs) == 5, f"expected 5 category cards on research/index.html, got {len(descs)}"


def test_old_decision_neuroscience_pages_removed_and_nfrsea_retained(site):
    for old_slug in ("dm", "decneurosc", "cogneuro"):
        assert not (REPO_ROOT / "research" / f"{old_slug}.qmd").exists(), (
            f"research/{old_slug}.qmd must be removed"
        )
        assert not (site / "research" / f"{old_slug}.html").exists(), (
            f"_site/research/{old_slug}.html must not exist"
        )

    assert (REPO_ROOT / "research" / "nfrsea.qmd").is_file(), "research/nfrsea.qmd must be retained"
    assert (site / "research" / "nfrsea.html").is_file(), "_site/research/nfrsea.html must be retained"


@pytest.mark.parametrize("slug,target", list(LEGACY_REDIRECTS.items()))
def test_legacy_project_redirects_all_seven(site, slug, target):
    redirect_page = site / "project" / slug / "index.html"
    expected_page = site / "research" / target
    assert redirect_page.is_file(), f"missing redirect page _site/project/{slug}/index.html"
    assert expected_page.is_file(), f"missing target page _site/research/{target}"
    assert redirects_to(site, redirect_page, expected_page), (
        f"_site/project/{slug}/index.html does not redirect to _site/research/{target}"
    )


# --- 2. Data Files Schema & No Grant Amounts -------------------------------


def test_projects_yml_schema_and_ten_cv_grants():
    assert PROJECTS_YML.is_file(), "data/projects.yml missing"
    projects = yaml.safe_load(PROJECTS_YML.read_text(encoding="utf8"))
    assert isinstance(projects, list), "data/projects.yml must be a YAML list"
    assert len(projects) == 10, f"expected 10 CV grants in data/projects.yml, got {len(projects)}"

    bib_keys = {e["key"] for e in _parse_bib_entries()}
    valid_cats = set(CANONICAL_CATEGORIES)
    seen_ids = set()
    non_empty_funders = []

    for proj in projects:
        assert isinstance(proj, dict), f"project entry must be a mapping: {proj!r}"
        pid = proj.get("id")
        assert isinstance(pid, str) and pid.strip() and not any(c.isspace() for c in pid), (
            f"invalid project id: {pid!r}"
        )
        assert pid not in seen_ids, f"duplicate project id: {pid!r}"
        seen_ids.add(pid)

        for field in ("title", "role", "period", "description"):
            val = proj.get(field)
            assert isinstance(val, str) and val.strip(), f"project {pid}: empty/invalid {field!r}"

        funder = proj.get("funder")
        assert isinstance(funder, str), f"project {pid}: funder must be a string, got {funder!r}"
        if funder.strip():
            non_empty_funders.append(funder.strip())

        year = proj.get("year")
        assert isinstance(year, int) and not isinstance(year, bool) and 1990 <= year <= 2099, (
            f"project {pid}: invalid 4-digit year {year!r}"
        )
        assert str(year) in proj["period"], (
            f"project {pid}: period {proj['period']!r} must contain year {year}"
        )

        status = proj.get("status")
        assert status in ("current", "past"), f"project {pid}: invalid status {status!r}"

        cats = proj.get("categories")
        assert isinstance(cats, list) and len(cats) >= 1, f"project {pid}: categories must be non-empty list"
        assert len(cats) == len(set(cats)), f"project {pid}: duplicate categories {cats!r}"
        assert set(cats) <= valid_cats, f"project {pid}: invalid categories {set(cats) - valid_cats}"

        papers = proj.get("papers")
        assert isinstance(papers, list), f"project {pid}: papers must be a list"
        for k in papers:
            assert k in bib_keys, f"project {pid}: unknown paper BibTeX key {k!r}"

    # All 4 explicit CV funder strings must appear across the 6 CV-funded projects
    assert len(non_empty_funders) >= 6, f"expected at least 6 projects with explicit CV funders, got {non_empty_funders!r}"
    for expected_funder in EXPLICIT_CV_FUNDERS:
        assert expected_funder in non_empty_funders, (
            f"exact CV funder string {expected_funder!r} missing from data/projects.yml funders {non_empty_funders!r}"
        )

    # The 2020 ADHD/special-education project links to nfrsea.html
    nfrsea_projects = [p for p in projects if p.get("url") == "nfrsea.html"]
    assert len(nfrsea_projects) == 1, "expected exactly 1 project with url='nfrsea.html'"
    assert nfrsea_projects[0]["id"] == "nfrsea-2020"


def test_no_grant_amounts_in_projects_yml_or_rendered_site(site):
    assert PROJECTS_YML.is_file(), "data/projects.yml missing"
    raw_yml = PROJECTS_YML.read_text(encoding="utf8")
    for amt in FORBIDDEN_GRANT_AMOUNTS:
        assert amt not in raw_yml, f"grant amount {amt!r} leaked in data/projects.yml"

    for page in sorted(site.rglob("*.html")):
        raw_html = page.read_text(encoding="utf8", errors="replace")
        rel = page.relative_to(site).as_posix()
        for amt in FORBIDDEN_GRANT_AMOUNTS:
            assert amt not in raw_html, f"grant amount {amt!r} leaked in {rel}"

    for slug in CANONICAL_CATEGORIES:
        page = site / "research" / f"{slug}.html"
        assert page.is_file(), f"_site/research/{slug}.html missing"
        for entry in soup_of(page).select("article.project-entry"):
            txt = collapse_ws(entry.get_text(" "))
            m = FORBIDDEN_PROJECT_CURRENCY_RE.search(txt)
            assert m is None, (
                f"currency/amount token {m.group(0)!r} found in project {entry.get('data-project-id')!r} "
                f"on research/{slug}.html"
            )


def test_publication_categories_yml_covers_all_69_bib_entries():
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"
    cat_map = yaml.safe_load(PUB_CATEGORIES_YML.read_text(encoding="utf8"))
    assert isinstance(cat_map, dict), "data/publication_categories.yml must be a YAML mapping"

    bib_keys = {e["key"] for e in _parse_bib_entries()}
    assert len(bib_keys) == 69

    map_keys = set(cat_map.keys())
    missing = bib_keys - map_keys
    orphans = map_keys - bib_keys
    assert not missing, f"BibTeX keys missing from data/publication_categories.yml: {sorted(missing)[:10]}"
    assert not orphans, f"orphan keys in data/publication_categories.yml: {sorted(orphans)[:10]}"

    valid_cats = set(CANONICAL_CATEGORIES)
    multi_cat_count = 0
    for key, cats in cat_map.items():
        assert isinstance(cats, list) and len(cats) >= 1, f"{key}: categories must be a non-empty list"
        assert len(cats) == len(set(cats)), f"{key}: duplicate categories {cats!r}"
        assert set(cats) <= valid_cats, f"{key}: invalid categories {set(cats) - valid_cats}"
        if len(cats) > 1:
            multi_cat_count += 1

    assert multi_cat_count >= 1, "expected at least some multi-category publications"
    assert "adhd" in cat_map["VarnetPerez2025"] and "causalinf" in cat_map["VarnetPerez2025"], (
        f"VarnetPerez2025 must belong to both 'adhd' and 'causalinf', got {cat_map['VarnetPerez2025']!r}"
    )


# --- 3. Rendered Category Pages --------------------------------------------


@pytest.mark.parametrize("slug", CANONICAL_CATEGORIES)
def test_category_pages_have_title_description_overview_and_images(site, slug):
    page = site / "research" / f"{slug}.html"
    assert page.is_file(), f"_site/research/{slug}.html missing"
    soup = soup_of(page)

    title_el = soup.title
    assert title_el is not None and CATEGORY_TITLES[slug] in collapse_ws(title_el.get_text()), (
        f"research/{slug}.html title missing {CATEGORY_TITLES[slug]!r}"
    )

    meta_desc = soup.find("meta", attrs={"name": "description"})
    assert meta_desc is not None and meta_desc.get("content", "").strip(), (
        f"research/{slug}.html missing non-empty meta description"
    )

    main = soup.find("main") or soup.body or soup
    overview_paras = [
        p for p in main.find_all("p")
        if p.find_parent("article", class_="project-entry") is None
        and p.find_parent("article", class_="pub-entry") is None
        and collapse_ws(p.get_text())
    ]
    assert overview_paras, f"research/{slug}.html has no overview prose paragraphs"

    imgs = main.find_all("img")
    assert imgs, f"research/{slug}.html has no images"
    for img in imgs:
        src = img.get("src") or ""
        assert src and resolve(site, page, src).is_file(), (
            f"broken image {src!r} on research/{slug}.html"
        )


@pytest.mark.parametrize("slug", CANONICAL_CATEGORIES)
def test_category_pages_render_projects_grouped_and_ordered(site, slug):
    assert PROJECTS_YML.is_file(), "data/projects.yml missing"
    projects = yaml.safe_load(PROJECTS_YML.read_text(encoding="utf8"))
    bib_by_key = {e["key"]: e for e in _parse_bib_entries()}

    page = site / "research" / f"{slug}.html"
    assert page.is_file(), f"_site/research/{slug}.html missing"
    soup = soup_of(page)
    main = soup.find("main") or soup.body or soup

    cat_projects = [p for p in projects if slug in p["categories"]]
    expected_current = sorted(
        [p for p in cat_projects if p["status"] == "current"],
        key=lambda p: -p["year"],
    )
    expected_past = sorted(
        [p for p in cat_projects if p["status"] == "past"],
        key=lambda p: -p["year"],
    )

    headings = [collapse_ws(h.get_text()).casefold() for h in main.find_all(["h2", "h3", "h4"])]
    has_current_heading = any("current projects" == h for h in headings)
    has_past_heading = any("past projects" == h for h in headings)

    if expected_current:
        assert has_current_heading, f"research/{slug}.html missing 'Current projects' heading"
    else:
        assert not has_current_heading, f"research/{slug}.html must omit empty 'Current projects' heading"

    if expected_past:
        assert has_past_heading, f"research/{slug}.html missing 'Past projects' heading"
    else:
        assert not has_past_heading, f"research/{slug}.html must omit empty 'Past projects' heading"

    # Contract examples: decision-making has only Past; causalinf has only Current
    if slug == "decision-making":
        assert not expected_current and expected_past
    if slug == "causalinf":
        assert expected_current and not expected_past

    rendered_entries = main.select("article.project-entry")
    rendered_current = [e for e in rendered_entries if e.get("data-status") == "current"]
    rendered_past = [e for e in rendered_entries if e.get("data-status") == "past"]

    assert [e.get("data-project-id") for e in rendered_current] == [p["id"] for p in expected_current], (
        f"research/{slug}.html current projects order/set mismatch"
    )
    assert [e.get("data-project-id") for e in rendered_past] == [p["id"] for p in expected_past], (
        f"research/{slug}.html past projects order/set mismatch"
    )

    by_id = {e.get("data-project-id"): e for e in rendered_entries}
    for proj in cat_projects:
        el = by_id[proj["id"]]
        txt = collapse_ws(el.get_text(" "))
        assert proj["title"] in txt, f"{slug}/{proj['id']}: missing title"
        assert proj["role"] in txt, f"{slug}/{proj['id']}: missing role"
        assert proj["period"] in txt, f"{slug}/{proj['id']}: missing period"
        assert proj["description"] in txt, f"{slug}/{proj['id']}: missing description"
        if proj.get("funder", "").strip():
            assert proj["funder"].strip() in txt, (
                f"{slug}/{proj['id']}: missing exact funder {proj['funder']!r}"
            )

        links = el.select("a[href]")
        detail_url = proj.get("url")
        if detail_url:
            expected_detail = resolve(site, page, detail_url).resolve()
            assert any(
                resolve(site, page, a["href"]).resolve() == expected_detail
                for a in links
            ), f"{slug}/{proj['id']}: missing project detail link to {detail_url!r}"

        paper_links = [
            a for a in links
            if not (detail_url and resolve(site, page, a["href"]).resolve() == resolve(site, page, detail_url).resolve())
        ]
        assert len(paper_links) >= len(proj["papers"]), (
            f"{slug}/{proj['id']}: expected at least {len(proj['papers'])} paper links, got {len(paper_links)}"
        )
        for pkey in proj["papers"]:
            bib_e = bib_by_key[pkey]
            doi = bib_e["fields"].get("doi", "").strip().casefold()
            url = bib_e["fields"].get("url", "").strip().casefold()
            fam = _strip_accents_norm(_first_author_family(bib_e["fields"].get("author", "")))
            title_n = _strip_accents_norm(bib_e["fields"].get("title", ""))
            matched = any(
                (doi and doi in a["href"].casefold())
                or (url and url in a["href"].casefold())
                or (pkey.casefold() in a["href"].casefold())
                or (fam and fam in _strip_accents_norm(a.get_text(" ")))
                or (title_n and title_n[:20] in _strip_accents_norm(a.get_text(" ")))
                for a in paper_links
            )
            assert matched, f"{slug}/{proj['id']}: no clickable link matching paper {pkey!r}"


@pytest.mark.parametrize("slug", CANONICAL_CATEGORIES)
def test_category_pages_render_matching_publications_by_descending_year(site, slug):
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"
    cat_map = yaml.safe_load(PUB_CATEGORIES_YML.read_text(encoding="utf8"))
    bib_by_key = {e["key"]: e for e in _parse_bib_entries()}

    expected_keys = {k for k, cats in cat_map.items() if slug in cats}
    assert expected_keys, f"no publications assigned to category {slug!r}"

    page = site / "research" / f"{slug}.html"
    assert page.is_file(), f"_site/research/{slug}.html missing"
    soup = soup_of(page)

    articles = soup.select("article.pub-entry")
    rendered_keys = [a.get("data-key", "") for a in articles]
    assert len(rendered_keys) == len(set(rendered_keys)), f"duplicate pub-entry keys on research/{slug}.html"
    assert set(rendered_keys) == expected_keys, (
        f"research/{slug}.html pub-entry mismatch: extra={set(rendered_keys) - expected_keys}, "
        f"missing={expected_keys - set(rendered_keys)}"
    )

    year_sections = soup.select("section.pub-year")
    assert year_sections, f"no section.pub-year on research/{slug}.html"
    years = [int(s.get("data-year", "0")) for s in year_sections]
    assert years == sorted(years, reverse=True) and len(years) == len(set(years)), (
        f"research/{slug}.html year sections not strictly descending: {years!r}"
    )

    for art in articles:
        key = art.get("data-key")
        bib_e = bib_by_key[key]
        assert art.get("data-year") == bib_e["fields"]["year"]
        expected_type = "preprint" if bib_e["type"] in PREPRINT_TYPES else bib_e["type"]
        assert art.get("data-pub-type") == expected_type
        parent_sec = art.find_parent("section", class_="pub-year")
        assert parent_sec is not None and parent_sec.get("data-year") == art.get("data-year")


def test_category_pages_internal_links_resolve(site):
    broken = []
    for slug in CANONICAL_CATEGORIES:
        page = site / "research" / f"{slug}.html"
        assert page.is_file(), f"_site/research/{slug}.html missing"
        for url in internal_urls(page):
            if not resolve(site, page, url).is_file():
                broken.append(f"research/{slug}.html: {url}")
    assert not broken, f"broken internal links on category pages:\n" + "\n".join(broken[:20])


# --- 4. Dynamic Rebuild & Validation ---------------------------------------


def test_dynamic_rebuild_propagates_synthetic_paper_and_project(tmp_path):
    assert PROJECTS_YML.is_file(), "data/projects.yml missing"
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"

    proj_dir = tmp_path / "proj"
    _copy_project(proj_dir)

    extra_bib = """

@article{SyntheticCat2027,
  title = {Synthetic Multi-Category Paper for Rebuild Check},
  author = {Biele, Guido and Doe, Jane},
  journal = {Journal of Synthetic Testing},
  year = {2027},
  doi = {10.5555/synthetic.cat.2027}
}
"""
    bib_path = proj_dir / "data" / "references.bib"
    bib_path.write_text(bib_path.read_text(encoding="utf8") + extra_bib, encoding="utf8")

    cat_path = proj_dir / "data" / "publication_categories.yml"
    cat_map = yaml.safe_load(cat_path.read_text(encoding="utf8"))
    # List categories in non-canonical order to verify canonical ordering in output
    cat_map["SyntheticCat2027"] = ["bayes", "causalinf"]
    cat_path.write_text(yaml.safe_dump(cat_map, sort_keys=False, allow_unicode=True), encoding="utf8")

    projects_path = proj_dir / "data" / "projects.yml"
    projects = yaml.safe_load(projects_path.read_text(encoding="utf8"))
    projects.append({
        "id": "synthetic-bayes-2027",
        "title": "Synthetic Bayesian Rebuild Project",
        "role": "PI",
        "funder": "NFR",
        "year": 2027,
        "period": "2027–present",
        "status": "current",
        "categories": ["bayes"],
        "description": "Synthetic project verifying dynamic category page rebuild.",
        "papers": ["SyntheticCat2027"],
    })
    projects_path.write_text(yaml.safe_dump(projects, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode == 0, f"quarto render failed:\n{proc.stderr[-2000:]}"

    out_site = proj_dir / "_site"

    # Publications page shows SyntheticCat2027 with canonical data-categories
    pub_soup = soup_of(out_site / "publications.html")
    pub_art = pub_soup.select_one('article.pub-entry[data-key="SyntheticCat2027"]')
    assert pub_art is not None
    assert pub_art.get("data-categories") == "causalinf bayes"

    # Category pages: present on bayes & causalinf, absent on adhd
    for slug in ("bayes", "causalinf"):
        cat_soup = soup_of(out_site / "research" / f"{slug}.html")
        assert cat_soup.select_one('article.pub-entry[data-key="SyntheticCat2027"]') is not None, (
            f"SyntheticCat2027 missing from research/{slug}.html"
        )
    adhd_soup = soup_of(out_site / "research" / "adhd.html")
    assert adhd_soup.select_one('article.pub-entry[data-key="SyntheticCat2027"]') is None

    # Synthetic project appears on research/bayes.html under current projects with clickable paper link
    bayes_soup = soup_of(out_site / "research" / "bayes.html")
    proj_el = bayes_soup.select_one('article.project-entry[data-project-id="synthetic-bayes-2027"]')
    assert proj_el is not None and proj_el.get("data-status") == "current"
    assert "Synthetic Bayesian Rebuild Project" in collapse_ws(proj_el.get_text(" "))
    assert any(a["href"] for a in proj_el.select("a[href]"))


def test_dynamic_rebuild_uncategorized_bib_entry_succeeds_with_warning(tmp_path):
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"

    proj_dir = tmp_path / "proj"
    _copy_project(proj_dir)

    extra_bib = """

@article{UncategorizedSynthetic2027,
  title = {Uncategorized Synthetic Article for Rebuild Compatibility},
  author = {Biele, Guido and Doe, Jane},
  journal = {Journal of Compatibility},
  year = {2027}
}
"""
    bib_path = proj_dir / "data" / "references.bib"
    bib_path.write_text(bib_path.read_text(encoding="utf8") + extra_bib, encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode == 0, f"quarto render should succeed for uncategorized bib entry:\n{proc.stderr[-2000:]}"
    assert "UncategorizedSynthetic2027" in proc.stderr or "warning" in proc.stderr.casefold(), (
        f"expected warning to stderr for uncategorized entry, got stderr:\n{proc.stderr[-1000:]}"
    )

    pub_soup = soup_of(proj_dir / "_site" / "publications.html")
    art = pub_soup.select_one('article.pub-entry[data-key="UncategorizedSynthetic2027"]')
    assert art is not None
    assert (art.get("data-categories") or "").strip() == ""


@pytest.mark.parametrize("mode", [
    "invalid_pub_category",
    "orphan_pub_key",
    "invalid_project_category",
    "unknown_project_paper",
])
def test_build_fails_on_invalid_category_or_orphan_keys(tmp_path, mode):
    assert PROJECTS_YML.is_file(), "data/projects.yml missing"
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"

    proj_dir = tmp_path / f"proj_{mode}"
    _copy_project(proj_dir)

    cat_path = proj_dir / "data" / "publication_categories.yml"
    projects_path = proj_dir / "data" / "projects.yml"

    if mode == "invalid_pub_category":
        cat_map = yaml.safe_load(cat_path.read_text(encoding="utf8"))
        first_key = next(iter(cat_map))
        cat_map[first_key] = ["not-a-valid-category"]
        cat_path.write_text(yaml.safe_dump(cat_map, sort_keys=False, allow_unicode=True), encoding="utf8")
    elif mode == "orphan_pub_key":
        cat_map = yaml.safe_load(cat_path.read_text(encoding="utf8"))
        cat_map["NonExistentBibKey9999"] = ["bayes"]
        cat_path.write_text(yaml.safe_dump(cat_map, sort_keys=False, allow_unicode=True), encoding="utf8")
    elif mode == "invalid_project_category":
        projects = yaml.safe_load(projects_path.read_text(encoding="utf8"))
        projects[0]["categories"] = ["bogus-category"]
        projects_path.write_text(yaml.safe_dump(projects, sort_keys=False, allow_unicode=True), encoding="utf8")
    elif mode == "unknown_project_paper":
        projects = yaml.safe_load(projects_path.read_text(encoding="utf8"))
        projects[0]["papers"] = ["NonExistentProjectPaper9999"]
        projects_path.write_text(yaml.safe_dump(projects, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode != 0, (
        f"quarto render unexpectedly succeeded for invalid data mode {mode!r}"
    )
