"""Visible tests for issue #12: research page organized by research interests with highlighted papers."""

import re
import shutil
import subprocess
import unicodedata

import pytest
import yaml

from site_helpers import (
    REPO_ROOT, collapse_ws, internal_urls, is_internal, norm, page_text,
    resolve, soup_of,
)

REFERENCES_BIB = REPO_ROOT / "data" / "references.bib"
RESEARCH_INTERESTS_YML = REPO_ROOT / "data" / "research_interests.yml"

EXPECTED_INTERESTS = [
    ("causal-inference", "Causal inference"),
    ("bayesian-statistics", "Bayesian statistics"),
    ("statistical-modelling", "Statistical modelling"),
    ("adhd", "ADHD"),
    ("cognitive-modelling", "Cognitive modelling"),
]

EXPECTED_MORE_LINKS = {
    "causal-inference": "causalinf.html",
    "bayesian-statistics": "bayes.html",
    "statistical-modelling": "epi.html",
    "adhd": "adhd.html",
    "cognitive-modelling": "decision-making.html",
}

CANONICAL_CATEGORY_HTML = set(EXPECTED_MORE_LINKS.values())
PAPER_LINK_LABELS = {"DOI", "PDF", "Preprint", "Proceedings", "Report", "URL"}
LEGACY_TYPOS = ("Epidemiologial", "special medication", "rsik factors", "sigfnificant", "reinfocement")

_LATEX_LIGATURES = {
    "aa": "å", "AA": "Å",
    "o": "ø", "O": "Ø",
    "ae": "æ", "AE": "Æ",
    "ss": "ß",
    "l": "ł", "L": "Ł",
    "i": "i", "j": "j",
}
_LATEX_ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
    "c": "\u0327", "r": "\u030a",
}


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


def _latex_to_unicode(text):
    text = text.replace("\\textbackslashtextquoteright", "'")
    for cmd, ch in (
        ("\\textendash", "-"), ("\\textemdash", "-"),
        ("\\textquoteright", "'"), ("\\textquoteleft", "'"),
        ("\\textquotedblright", '"'), ("\\textquotedblleft", '"'),
    ):
        text = text.replace(cmd, ch)
    for k, v in _LATEX_LIGATURES.items():
        text = re.sub(rf"\{{\\{k}\}}|\\{k}(?![A-Za-z])", v, text)
    text = re.sub(
        r"\{\\(['`^\"~=.]|[uvHcr])\{?([A-Za-z])\}?\}|"
        r"\\(['`^\"~=.]|[uvHcr])\{([A-Za-z])\}|"
        r"\\(['`^~=.])([A-Za-z])|"
        r"\\([uvHcr])\s+([A-Za-z])",
        lambda m: unicodedata.normalize(
            "NFC",
            (m.group(2) or m.group(4) or m.group(6) or m.group(8))
            + _LATEX_ACCENTS[m.group(1) or m.group(3) or m.group(5) or m.group(7)],
        ),
        text,
    )
    text = re.sub(r"\\([&%$#_{}])", r"\1", text)
    return text.replace("{", "").replace("}", "")


def _first_author_family(author_field):
    first = _latex_to_unicode(author_field.split(" and ")[0].strip())
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


def _extract_link_target(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("href", "url", "target", "page"):
            val = item.get(key)
            if isinstance(val, str):
                return val
    return None


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


# --- 1. Rendered research/index.html structure and order -------------------


def test_research_index_five_interest_sections_match_about_order(site):
    about_soup = soup_of(site / "index.html")
    about_interests = [collapse_ws(li.get_text(" ")) for li in about_soup.select("section#interests ul > li")]
    expected_titles = [title for _, title in EXPECTED_INTERESTS]
    expected_ids = [slug for slug, _ in EXPECTED_INTERESTS]
    assert about_interests == expected_titles, (
        f"About page interests {about_interests!r} do not match expected {expected_titles!r}"
    )

    research_soup = soup_of(site / "research" / "index.html")
    main = research_soup.find("main")
    assert main is not None, "missing <main> on research/index.html"

    # Old category card grid must be replaced
    assert not research_soup.select("#listing-listing .quarto-grid-item"), (
        "old category card grid (.quarto-grid-item) still present on research/index.html"
    )
    assert not research_soup.select("#listing-listing .listing-description"), (
        "old category card grid (.listing-description) still present on research/index.html"
    )

    sections = main.select("section.research-interest")
    assert len(sections) == 5, f"expected 5 section.research-interest elements, got {len(sections)}"

    actual_ids = [s.get("data-interest-id") for s in sections]
    assert actual_ids == expected_ids, f"expected data-interest-id order {expected_ids}, got {actual_ids}"

    actual_headings = []
    for sec in sections:
        h2 = sec.find("h2")
        assert h2 is not None, f"missing <h2> in section {sec.get('data-interest-id')!r}"
        actual_headings.append(collapse_ws(h2.get_text(" ")))

    assert actual_headings == about_interests, (
        f"research/index.html headings {actual_headings!r} != About interests {about_interests!r}"
    )


# --- 2. One-paragraph description per interest -----------------------------


def test_research_index_one_paragraph_description_per_interest(site):
    page = site / "research" / "index.html"
    soup = soup_of(page)
    main = soup.find("main")
    assert main is not None

    sections = main.select("section.research-interest")
    assert len(sections) == 5

    for sec in sections:
        sid = sec.get("data-interest-id")
        descs = sec.select("p.research-interest-description")
        assert len(descs) == 1, (
            f"section {sid!r} must contain exactly 1 p.research-interest-description, got {len(descs)}"
        )
        desc_text = collapse_ws(descs[0].get_text(" "))
        assert len(desc_text.split()) >= 10, (
            f"section {sid!r} description looks too short/empty: {desc_text!r}"
        )

    full_text = page_text(page)
    for bad in LEGACY_TYPOS:
        assert bad not in full_text, f"legacy typo {bad!r} found on research/index.html"


# --- 3. Highlighted representative papers from data/references.bib ---------


def test_research_index_highlighted_papers_match_bib_and_have_links(site):
    bib_by_key = {e["key"]: e for e in _parse_bib_entries()}
    page = site / "research" / "index.html"
    soup = soup_of(page)
    main = soup.find("main")
    assert main is not None

    sections = main.select("section.research-interest")
    assert len(sections) == 5

    bad_tex_re = re.compile(r"\\(?:[A-Za-z]+|[&%$#_{}~^'\"`])")
    bad_math_re = re.compile(r"\$(?:\\[A-Za-z]+|[A-Za-z][ _^=<>+\-*/\\A-Za-z0-9]{0,20})\$")
    bad_entity_re = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")

    for sec in sections:
        sid = sec.get("data-interest-id")
        paper_list = sec.select_one("ul.highlighted-papers-list")
        assert paper_list is not None, f"section {sid!r} missing ul.highlighted-papers-list"

        items = paper_list.select("li.highlighted-paper")
        assert 2 <= len(items) <= 3, (
            f"section {sid!r} must have 2 to 3 highlighted papers, got {len(items)}"
        )

        seen_keys = set()
        for item in items:
            key = item.get("data-key")
            assert key, f"highlighted paper in {sid!r} missing data-key"
            assert key in bib_by_key, f"section {sid!r} references unknown BibTeX key {key!r}"
            assert key not in seen_keys, f"duplicate highlighted paper key {key!r} in section {sid!r}"
            seen_keys.add(key)

            entry = bib_by_key[key]
            fields = entry["fields"]
            item_text = collapse_ws(item.get_text(" "))

            # Clean Unicode text: no raw LaTeX, braces, or double-escaped entities
            assert "{" not in item_text and "}" not in item_text, f"raw braces in {key}: {item_text!r}"
            assert not bad_tex_re.search(item_text), f"raw LaTeX in {key}: {item_text!r}"
            assert not bad_math_re.search(item_text), f"raw TeX math in {key}: {item_text!r}"
            assert not bad_entity_re.search(item_text), f"double-escaped entity in {key}: {item_text!r}"

            # Title matches BibTeX entry
            bib_title = _latex_to_unicode(fields["title"])
            assert norm(bib_title) in norm(item_text), (
                f"title for {key} ({bib_title!r}) not found in rendered item {item_text!r}"
            )

            # Year matches BibTeX entry
            bib_year = fields["year"].strip()
            assert bib_year in item_text, f"year {bib_year!r} for {key} not found in {item_text!r}"

            # Venue matches BibTeX entry (when a venue field is present)
            raw_venue = (
                fields.get("journal")
                or fields.get("booktitle")
                or fields.get("institution")
                or fields.get("howpublished")
                or fields.get("publisher")
            )
            if raw_venue:
                bib_venue = _latex_to_unicode(raw_venue)
                assert norm(bib_venue) in norm(item_text), (
                    f"venue {bib_venue!r} for {key} not found in rendered item {item_text!r}"
                )

            # Authors: first author family name present & Guido Biele highlighted in strong.pub-author-self
            first_family = _first_author_family(fields["author"])
            assert _strip_accents_norm(first_family) in _strip_accents_norm(item_text), (
                f"first author {first_family!r} for {key} not found in {item_text!r}"
            )
            selves = item.select("strong.pub-author-self")
            assert len(selves) == 1, f"expected 1 strong.pub-author-self in {key}, got {len(selves)}"
            self_text = collapse_ws(selves[0].get_text(" "))
            lower_self = self_text.casefold()
            assert "biele" in lower_self and re.search(r"\bguido\b|\bg\.?(?=\s|$)", lower_self), (
                f"unexpected strong.pub-author-self text in {key}: {self_text!r}"
            )
            assert " and " not in lower_self and len(self_text.split()) <= 5, (
                f"co-authors leaked into strong.pub-author-self in {key}: {self_text!r}"
            )

            # At least one non-empty clickable paper link (not Code-only) derived from BibTeX
            pub_links = item.select("a.pub-link[href]")
            paper_links = [
                a for a in pub_links
                if a["href"].strip() and collapse_ws(a.get_text(" ")) in PAPER_LINK_LABELS
            ]
            assert paper_links, (
                f"highlighted paper {key} in {sid!r} has no clickable paper link "
                f"(a.pub-link with label in {sorted(PAPER_LINK_LABELS)})"
            )

            for a in pub_links:
                href = a["href"].strip()
                label = collapse_ws(a.get_text(" "))
                assert href, f"empty href on a.pub-link in {key}"
                if label == "DOI":
                    assert fields.get("doi"), f"DOI link rendered for {key} without doi in BibTeX"
                    expected_doi = f"https://doi.org/{fields['doi'].strip().casefold()}"
                    assert href.casefold() == expected_doi, (
                        f"DOI link mismatch for {key}: got {href!r}, expected {expected_doi!r}"
                    )
                if is_internal(href):
                    resolved = resolve(site, page, href)
                    assert resolved.is_file(), (
                        f"broken internal paper link {href!r} in {key} on research/index.html (resolved {resolved})"
                    )


# --- 4. Deeper category page links and no nfrsea top-level -----------------


def test_research_index_category_more_links_and_no_nfrsea(site):
    page = site / "research" / "index.html"
    soup = soup_of(page)
    main = soup.find("main")
    assert main is not None

    sections = main.select("section.research-interest")
    assert len(sections) == 5

    nfrsea_target = (site / "research" / "nfrsea.html").resolve()
    for a in main.find_all("a", href=True):
        href = a["href"]
        if is_internal(href):
            resolved = resolve(site, page, href).resolve()
            assert resolved != nfrsea_target, "nfrsea.html must not be linked from research/index.html <main>"
            assert resolved.is_file(), f"broken internal link {href!r} on research/index.html"

    for sec in sections:
        sid = sec.get("data-interest-id")
        more = sec.select_one("div.research-interest-more")
        assert more is not None, f"section {sid!r} missing div.research-interest-more"
        expected_target = (site / "research" / EXPECTED_MORE_LINKS[sid]).resolve()
        more_targets = [
            resolve(site, page, a["href"]).resolve()
            for a in more.find_all("a", href=True)
            if is_internal(a["href"])
        ]
        assert expected_target in more_targets, (
            f"section {sid!r} div.research-interest-more does not link to {EXPECTED_MORE_LINKS[sid]!r}"
        )

    for url in internal_urls(page):
        assert resolve(site, page, url).is_file(), f"broken internal URL {url!r} on research/index.html"


# --- 5. Data file data/research_interests.yml schema -----------------------


def test_research_interests_yml_schema_and_order():
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"
    data = yaml.safe_load(RESEARCH_INTERESTS_YML.read_text(encoding="utf8"))
    assert isinstance(data, list), "data/research_interests.yml must be a YAML list"
    assert len(data) == 5, f"expected 5 entries in data/research_interests.yml, got {len(data)}"

    bib_keys = {e["key"] for e in _parse_bib_entries()}
    actual_pairs = []
    for entry in data:
        assert isinstance(entry, dict), f"interest entry must be a mapping: {entry!r}"
        iid = entry.get("id")
        title = entry.get("title")
        desc = entry.get("description")
        links = entry.get("links")
        papers = entry.get("papers")

        assert isinstance(iid, str) and iid.strip(), f"invalid id: {iid!r}"
        assert isinstance(title, str) and title.strip(), f"invalid title for {iid!r}: {title!r}"
        assert isinstance(desc, str) and desc.strip(), f"empty description for {iid!r}"
        actual_pairs.append((iid, title))

        assert isinstance(links, list) and len(links) >= 1, f"invalid links for {iid!r}: {links!r}"
        for item in links:
            target = _extract_link_target(item)
            assert target in CANONICAL_CATEGORY_HTML, (
                f"interest {iid!r} has invalid link target {target!r} (allowed: {sorted(CANONICAL_CATEGORY_HTML)})"
            )

        assert isinstance(papers, list) and 2 <= len(papers) <= 3, (
            f"interest {iid!r} must have 2 to 3 papers, got {papers!r}"
        )
        assert len(papers) == len(set(papers)), f"duplicate paper keys in {iid!r}: {papers!r}"
        for k in papers:
            assert k in bib_keys, f"interest {iid!r} references unknown BibTeX key {k!r}"

    assert actual_pairs == EXPECTED_INTERESTS, (
        f"data/research_interests.yml order/titles {actual_pairs!r} != expected {EXPECTED_INTERESTS!r}"
    )


# --- 6. Single-source dynamic rebuild --------------------------------------


def test_dynamic_rebuild_new_bib_entry_in_research_interests(tmp_path):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / "proj"
    _copy_project(proj_dir)

    extra_bib = """

@article{SyntheticInterest2027,
  author = {S{\\o}rensen, Astrid and Biele, Guido},
  title = {{Synthetic Causal Inference Highlight for Dynamic Rebuild}},
  journal = {Journal of Reproducible Research},
  year = {2027},
  doi = {10.5555/synthetic-interest-2027}
}
"""
    bib_path = proj_dir / "data" / "references.bib"
    bib_path.write_text(bib_path.read_text(encoding="utf8") + extra_bib, encoding="utf8")

    yml_path = proj_dir / "data" / "research_interests.yml"
    interests = yaml.safe_load(yml_path.read_text(encoding="utf8"))
    interests[0]["papers"] = ["SyntheticInterest2027"] + interests[0]["papers"][1:]
    yml_path.write_text(yaml.safe_dump(interests, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode == 0, f"quarto render failed:\n{proc.stderr[-2000:]}"

    soup = soup_of(proj_dir / "_site" / "research" / "index.html")
    item = soup.select_one(
        'section.research-interest[data-interest-id="causal-inference"] '
        'li.highlighted-paper[data-key="SyntheticInterest2027"]'
    )
    assert item is not None, "SyntheticInterest2027 did not appear in causal-inference section"
    text = collapse_ws(item.get_text(" "))
    assert "Synthetic Causal Inference Highlight for Dynamic Rebuild" in text
    assert "Sørensen" in text
    assert "Journal of Reproducible Research" in text
    assert "2027" in text
    selves = item.select("strong.pub-author-self")
    assert len(selves) == 1 and "biele" in selves[0].get_text().casefold()
    doi_links = [
        a for a in item.select("a.pub-link[href]")
        if collapse_ws(a.get_text(" ")) == "DOI"
        and a["href"].strip().casefold() == "https://doi.org/10.5555/synthetic-interest-2027"
    ]
    assert doi_links, f"expected DOI link for SyntheticInterest2027, got {item.select('a.pub-link')}"


# --- 7. Build validation on invalid data/research_interests.yml ------------


@pytest.mark.parametrize("mode", [
    "unknown_paper_key",
    "too_few_papers",
    "too_many_papers",
    "duplicate_id",
    "duplicate_paper_key",
    "empty_title",
    "empty_description",
    "invalid_link_target",
])
def test_build_fails_on_invalid_research_interests_yml(tmp_path, mode):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / f"proj_{mode}"
    _copy_project(proj_dir)

    yml_path = proj_dir / "data" / "research_interests.yml"
    interests = yaml.safe_load(yml_path.read_text(encoding="utf8"))
    bib_keys = [e["key"] for e in _parse_bib_entries(proj_dir / "data" / "references.bib")]

    if mode == "unknown_paper_key":
        interests[0]["papers"][0] = "NonExistentInterestPaper9999"
    elif mode == "too_few_papers":
        interests[0]["papers"] = interests[0]["papers"][:1]
    elif mode == "too_many_papers":
        extra = [k for k in bib_keys if k not in interests[0]["papers"]]
        interests[0]["papers"] = interests[0]["papers"][:2] + extra[:2]
    elif mode == "duplicate_id":
        interests[1]["id"] = interests[0]["id"]
    elif mode == "duplicate_paper_key":
        interests[0]["papers"] = [interests[0]["papers"][0], interests[0]["papers"][0]]
    elif mode == "empty_title":
        interests[0]["title"] = ""
    elif mode == "empty_description":
        interests[0]["description"] = ""
    elif mode == "invalid_link_target":
        interests[0]["links"][0] = _replace_link_target(interests[0]["links"][0], "nonexistent.html")

    yml_path.write_text(yaml.safe_dump(interests, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode != 0, (
        f"quarto render unexpectedly succeeded for invalid research_interests.yml mode {mode!r}"
    )
