"""Visible tests for issues #12 & #15: research interest cards with custom SVGs and topic-page overviews."""

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest
import yaml

from site_helpers import (
    REPO_ROOT, collapse_ws, internal_urls, is_internal, norm, page_text,
    resolve, soup_of,
)

REFERENCES_BIB = REPO_ROOT / "data" / "references.bib"
RESEARCH_INTERESTS_YML = REPO_ROOT / "data" / "research_interests.yml"
STYLES_CSS = REPO_ROOT / "styles.css"

EXPECTED_INTERESTS = [
    ("causal-inference", "Causal inference"),
    ("bayesian-statistics", "Bayesian statistics"),
    ("statistical-modelling", "Statistical modelling"),
    ("adhd", "ADHD"),
    ("cognitive-modelling", "Cognitive modelling"),
]

EXPECTED_TOPIC_LINKS = {
    "causal-inference": "causalinf.html",
    "bayesian-statistics": "bayes.html",
    "statistical-modelling": "epi.html",
    "adhd": "adhd.html",
    "cognitive-modelling": "decision-making.html",
}

CANONICAL_CATEGORY_HTML = set(EXPECTED_TOPIC_LINKS.values())
PAPER_LINK_LABELS = {"DOI", "PDF", "Preprint", "Proceedings", "Report", "URL"}
LEGACY_TYPOS = ("Epidemiologial", "special medication", "rsik factors", "sigfnificant", "reinfocement")

SVG_PRIMITIVE_TAGS = ("path", "circle", "rect", "line", "polygon", "ellipse", "polyline")
SVG_PRESENTATION_COLOR_ATTRS = ("fill", "stroke", "stop-color", "color", "flood-color", "lighting-color")

# Contract-specified conceptual label groups required in visible <text> elements per topic SVG
EXPECTED_SVG_CONCEPT_GROUPS = {
    "causal-inference": [
        ("DAG / graphical causal model", re.compile(r"\bdag\b|causal\s+(?:graph|model)|graphical", re.I)),
        ("estimator / estimation", re.compile(r"estimat", re.I)),
    ],
    "bayesian-statistics": [
        ("prior", re.compile(r"\bprior\b", re.I)),
        ("likelihood", re.compile(r"\blikelihood\b", re.I)),
        ("posterior", re.compile(r"\bposterior\b", re.I)),
    ],
    "statistical-modelling": [
        ("GLM", re.compile(r"\bglms?\b|generalized\s+linear", re.I)),
        ("splines / Gaussian processes", re.compile(r"spline|\bgps?\b|gaussian\s+process", re.I)),
        ("hierarchical modelling", re.compile(r"hierarch|multilevel|multi-level", re.I)),
    ],
    "adhd": [
        ("ADHD", re.compile(r"\badhd\b", re.I)),
        ("treatment", re.compile(r"treat|medicat|intervent", re.I)),
        ("outcomes", re.compile(r"outcome", re.I)),
    ],
    "cognitive-modelling": [
        ("drift diffusion / DDM", re.compile(r"drift|diffusion|\bddm\b", re.I)),
        ("brain", re.compile(r"\bbrain\b|neural|fmri|eeg", re.I)),
        ("behavior / behavioral", re.compile(r"behav", re.I)),
    ],
}

_PURE_BW_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:#000000|#000(?![0-9a-fA-F])|#ffffff|#fff(?![0-9a-fA-F])"
    r"|\bblack\b|\bwhite\b|rgb\(\s*0\s*,\s*0\s*,\s*0\s*\)|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))",
    re.I,
)

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


def _strip_var_calls(css_text):
    """Remove balanced `var(...)` calls so fallback values inside var() are not flagged."""
    out = []
    i = 0
    lower = css_text.lower()
    while i < len(css_text):
        idx = lower.find("var(", i)
        if idx == -1:
            out.append(css_text[i:])
            break
        out.append(css_text[i:idx])
        depth = 1
        j = idx + 4
        while j < len(css_text) and depth > 0:
            if css_text[j] == "(":
                depth += 1
            elif css_text[j] == ")":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def _strip_css_comments(css_text):
    return re.sub(r"/\*.*?\*/", "", css_text, flags=re.S)


def _parse_css_blocks(css_text):
    """Return list of (selector_str, body_str) for top-level and @media CSS rule blocks."""
    clean = _strip_css_comments(css_text)
    blocks = []

    def _walk(text):
        i = 0
        n = len(text)
        while i < n:
            brace = text.find("{", i)
            if brace == -1:
                break
            selector = text[i:brace].strip()
            depth = 1
            j = brace + 1
            while j < n and depth > 0:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            body = text[brace + 1:j - 1]
            if selector.startswith("@"):
                _walk(body)
            else:
                blocks.append((selector, body))
            i = j

    _walk(clean)
    return blocks


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


# --- 1. Rendered research/index.html card grid structure and order ---------


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

    # Old default Quarto grid and old vertical interest list must not be present
    assert not research_soup.select("#listing-listing .quarto-grid-item"), (
        "old category card grid (.quarto-grid-item) still present on research/index.html"
    )
    assert not research_soup.select("#listing-listing .listing-description"), (
        "old category card grid (.listing-description) still present on research/index.html"
    )
    assert not main.select("section.research-interest"), (
        "old vertical section.research-interest list still present on research/index.html"
    )
    assert not main.select("p.research-interest-description"), (
        "p.research-interest-description must not render on research/index.html cards"
    )
    assert not main.select("ul.highlighted-papers-list, li.highlighted-paper"), (
        "highlighted papers must not render on research/index.html cards"
    )
    assert not main.select("div.research-interest-more"), (
        "div.research-interest-more must not render on research/index.html cards"
    )

    grid = main.select_one(".research-cards-grid")
    assert grid is not None, "missing .research-cards-grid container in research/index.html <main>"

    cards = grid.select("article.research-card")
    assert len(cards) == 5, f"expected 5 article.research-card elements, got {len(cards)}"

    actual_ids = [c.get("data-interest-id") for c in cards]
    assert actual_ids == expected_ids, f"expected data-interest-id order {expected_ids}, got {actual_ids}"

    actual_headings = []
    for card in cards:
        cid = card.get("data-interest-id")
        link = card.select_one("a.research-card-link[href]")
        assert link is not None, f"card {cid!r} missing clickable a.research-card-link[href]"
        graphic = link.select_one(".research-card-graphic")
        assert graphic is not None, f"card {cid!r} a.research-card-link missing .research-card-graphic"
        h2 = link.select_one("h2.research-card-title")
        assert h2 is not None, f"card {cid!r} a.research-card-link missing h2.research-card-title"
        actual_headings.append(collapse_ws(h2.get_text(" ")))

        # No data-* attribute on card or any ancestor leaks raw <svg markup
        for el in [card, *card.parents]:
            if getattr(el, "attrs", None):
                for attr_name, attr_val in el.attrs.items():
                    if attr_name.startswith("data-"):
                        val_str = " ".join(attr_val) if isinstance(attr_val, list) else str(attr_val)
                        assert "<svg" not in val_str.casefold(), (
                            f"raw <svg markup leaked into {attr_name!r} on <{el.name}> for card {cid!r}"
                        )

    assert actual_headings == about_interests, (
        f"research/index.html card titles {actual_headings!r} != About interests {about_interests!r}"
    )

    # All id attributes inside <body> of research/index.html must be unique
    # (Quarto 1.11.9 emits duplicate <link id="quarto-bootstrap"> /
    # <link id="quarto-text-highlighting-styles"> in <head> for light+dark themes)
    body = research_soup.body
    assert body is not None, "research/index.html missing <body>"
    all_ids = [el.get("id") for el in body.find_all(id=True) if el.get("id")]
    duplicates = sorted({i for i in all_ids if all_ids.count(i) > 1})
    assert not duplicates, f"duplicate id attributes in research/index.html <body>: {duplicates!r}"


# --- 2. One-paragraph description per interest on linked topic pages -------


def test_research_index_one_paragraph_description_per_interest(site):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"
    interests = yaml.safe_load(RESEARCH_INTERESTS_YML.read_text(encoding="utf8"))
    by_id = {entry["id"]: entry for entry in interests}

    index_text = page_text(site / "research" / "index.html")
    for bad in LEGACY_TYPOS:
        assert bad not in index_text, f"legacy typo {bad!r} found on research/index.html"

    for sid, _ in EXPECTED_INTERESTS:
        slug_html = EXPECTED_TOPIC_LINKS[sid]
        page = site / "research" / slug_html
        assert page.is_file(), f"_site/research/{slug_html} missing"
        soup = soup_of(page)
        main = soup.find("main")
        assert main is not None, f"missing <main> on research/{slug_html}"

        sec = main.select_one(f'section.research-interest[data-interest-id="{sid}"]')
        assert sec is not None, (
            f"research/{slug_html} missing section.research-interest[data-interest-id={sid!r}]"
        )
        descs = sec.select("p.research-interest-description")
        assert len(descs) == 1, (
            f"research/{slug_html} section {sid!r} must contain exactly 1 p.research-interest-description, "
            f"got {len(descs)}"
        )
        desc_text = collapse_ws(descs[0].get_text(" "))
        assert len(desc_text.split()) >= 10, (
            f"research/{slug_html} section {sid!r} description looks too short/empty: {desc_text!r}"
        )
        expected_desc = collapse_ws(by_id[sid]["description"])
        assert norm(expected_desc) == norm(desc_text), (
            f"research/{slug_html} description does not match data/research_interests.yml for {sid!r}"
        )
        for bad in LEGACY_TYPOS:
            assert bad not in desc_text, f"legacy typo {bad!r} found in description on research/{slug_html}"


# --- 3. Highlighted representative papers on linked topic pages ------------


def test_research_index_highlighted_papers_match_bib_and_have_links(site):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"
    interests = yaml.safe_load(RESEARCH_INTERESTS_YML.read_text(encoding="utf8"))
    by_id = {entry["id"]: entry for entry in interests}
    bib_by_key = {e["key"]: e for e in _parse_bib_entries()}

    bad_tex_re = re.compile(r"\\(?:[A-Za-z]+|[&%$#_{}~^'\"`])")
    bad_math_re = re.compile(r"\$(?:\\[A-Za-z]+|[A-Za-z][ _^=<>+\-*/\\A-Za-z0-9]{0,20})\$")
    bad_entity_re = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")

    for sid, _ in EXPECTED_INTERESTS:
        slug_html = EXPECTED_TOPIC_LINKS[sid]
        page = site / "research" / slug_html
        soup = soup_of(page)
        main = soup.find("main")
        assert main is not None

        sec = main.select_one(f'section.research-interest[data-interest-id="{sid}"]')
        assert sec is not None, f"research/{slug_html} missing section.research-interest[data-interest-id={sid!r}]"

        # Highlighted papers must NOT use article.pub-entry
        assert not sec.select("article.pub-entry"), (
            f"section.research-interest on research/{slug_html} must use li.highlighted-paper, not article.pub-entry"
        )

        paper_list = sec.select_one("ul.highlighted-papers-list")
        assert paper_list is not None, f"section {sid!r} on research/{slug_html} missing ul.highlighted-papers-list"

        items = paper_list.select("li.highlighted-paper")
        assert 2 <= len(items) <= 3, (
            f"section {sid!r} on research/{slug_html} must have 2 to 3 highlighted papers, got {len(items)}"
        )

        expected_keys = list(by_id[sid]["papers"])
        actual_keys = [item.get("data-key") for item in items]
        assert actual_keys == expected_keys, (
            f"section {sid!r} on research/{slug_html} paper keys {actual_keys!r} != yml {expected_keys!r}"
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
                f"highlighted paper {key} in {sid!r} on research/{slug_html} has no clickable paper link "
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
                        f"broken internal paper link {href!r} in {key} on research/{slug_html} (resolved {resolved})"
                    )


# --- 4. Card links to canonical topic pages and no nfrsea top-level --------


def test_research_index_category_more_links_and_no_nfrsea(site):
    page = site / "research" / "index.html"
    soup = soup_of(page)
    main = soup.find("main")
    assert main is not None

    cards = main.select(".research-cards-grid article.research-card")
    assert len(cards) == 5

    nfrsea_target = (site / "research" / "nfrsea.html").resolve()
    for a in main.find_all("a", href=True):
        href = a["href"]
        if is_internal(href):
            resolved = resolve(site, page, href).resolve()
            assert resolved != nfrsea_target, "nfrsea.html must not be linked from research/index.html <main>"
            assert resolved.is_file(), f"broken internal link {href!r} on research/index.html"

    for card in cards:
        sid = card.get("data-interest-id")
        link = card.select_one("a.research-card-link[href]")
        assert link is not None, f"card {sid!r} missing a.research-card-link[href]"
        href = link["href"]
        assert is_internal(href), f"card {sid!r} link must be internal, got {href!r}"
        expected_target = (site / "research" / EXPECTED_TOPIC_LINKS[sid]).resolve()
        actual_target = resolve(site, page, href).resolve()
        assert actual_target == expected_target, (
            f"card {sid!r} links to {actual_target}, expected {expected_target}"
        )

    for url in internal_urls(page):
        assert resolve(site, page, url).is_file(), f"broken internal URL {url!r} on research/index.html"


# --- 5. Custom conceptual SVG graphics (structure, distinctness, labels) ---


def test_research_cards_conceptual_svg_graphics_structure_and_labels(site):
    page = site / "research" / "index.html"
    soup = soup_of(page)
    cards = soup.select("main .research-cards-grid article.research-card")
    assert len(cards) == 5, f"expected 5 research cards, got {len(cards)}"

    svg_markups = []
    for card in cards:
        cid = card.get("data-interest-id")
        graphic = card.select_one(".research-card-graphic")
        assert graphic is not None, f"card {cid!r} missing .research-card-graphic"

        svgs = graphic.find_all("svg")
        assert len(svgs) == 1, f"card {cid!r} .research-card-graphic must contain exactly 1 <svg>, got {len(svgs)}"
        svg = svgs[0]

        # Valid 4-number viewBox with width > 0 and height > 0
        viewbox = (svg.get("viewbox") or svg.get("viewBox") or "").strip()
        parts = re.split(r"[\s,]+", viewbox)
        assert len(parts) == 4, f"card {cid!r} <svg> has invalid viewBox {viewbox!r}"
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            pytest.fail(f"card {cid!r} <svg> has non-numeric viewBox {viewbox!r}")
        assert nums[2] > 0 and nums[3] > 0, (
            f"card {cid!r} <svg> viewBox must have positive width and height, got {viewbox!r}"
        )

        # At least 5 vector geometry primitives
        primitives = svg.find_all(list(SVG_PRIMITIVE_TAGS))
        assert len(primitives) >= 5, (
            f"card {cid!r} <svg> must contain at least 5 vector primitives, got {len(primitives)}"
        )

        # Accessible label via role="img" + aria-label OR <title>
        has_role_aria = (
            (svg.get("role") or "").strip() == "img"
            and bool((svg.get("aria-label") or "").strip())
        )
        title_el = svg.find("title")
        has_title = title_el is not None and bool(collapse_ws(title_el.get_text(" ")))
        assert has_role_aria or has_title, (
            f"card {cid!r} <svg> must have an accessible label (role='img' + aria-label, or non-empty <title>)"
        )

        # Visible <text> labels reflecting topic's conceptual diagram
        text_nodes = [
            collapse_ws(t.get_text(" "))
            for t in svg.find_all("text")
            if collapse_ws(t.get_text(" "))
        ]
        assert text_nodes, f"card {cid!r} <svg> must contain visible <text> labels"
        combined_labels = " | ".join(text_nodes)
        for concept_name, pattern in EXPECTED_SVG_CONCEPT_GROUPS[cid]:
            assert pattern.search(combined_labels), (
                f"card {cid!r} <svg> visible <text> labels {text_nodes!r} missing concept {concept_name!r}"
            )

        svg_markups.append(collapse_ws(str(svg)))

    assert len(set(svg_markups)) == 5, "all 5 research cards must have distinct inline <svg> diagrams"


# --- 6. SVG theme compatibility and safety ---------------------------------


def test_research_cards_svg_theme_compatibility_and_safety(site):
    page = site / "research" / "index.html"
    soup = soup_of(page)
    cards = soup.select("main .research-cards-grid article.research-card")
    assert len(cards) == 5, f"expected 5 research cards, got {len(cards)}"

    css_text = STYLES_CSS.read_text(encoding="utf8") if STYLES_CSS.is_file() else ""
    css_blocks = _parse_css_blocks(css_text)
    theme_styled_classes = set()
    for selector, body in css_blocks:
        if "var(--" in body or "currentcolor" in body.lower():
            for cls in re.findall(r"\.([A-Za-z_-][A-Za-z0-9_-]*)", selector):
                theme_styled_classes.add(cls)

    for card in cards:
        cid = card.get("data-interest-id")
        svg = card.select_one(".research-card-graphic svg")
        assert svg is not None, f"card {cid!r} missing inline <svg>"

        # Safety: no <script>, no javascript: URLs, no inline on* handlers
        assert not svg.find_all("script"), f"card {cid!r} <svg> must not contain <script>"
        uses_theme_mechanism = False

        for el in [svg, *svg.find_all(True)]:
            for attr_name, attr_val in list(el.attrs.items()):
                aname = attr_name.lower()
                val = " ".join(attr_val) if isinstance(attr_val, list) else str(attr_val)
                val_stripped = val.strip()

                assert not aname.startswith("on"), (
                    f"card {cid!r} <svg> contains inline event handler {attr_name!r} on <{el.name}>"
                )
                assert "javascript:" not in val_stripped.lower(), (
                    f"card {cid!r} <svg> contains javascript: URL in {attr_name!r} on <{el.name}>"
                )

                if aname in SVG_PRESENTATION_COLOR_ATTRS:
                    assert "var(" not in val_stripped.lower(), (
                        f"card {cid!r} <svg> must not use var(...) inside SVG presentation attribute "
                        f"{attr_name}={val_stripped!r}; use CSS classes or style property instead"
                    )
                    assert not _PURE_BW_RE.search(val_stripped), (
                        f"card {cid!r} <svg> uses un-themed hardcoded black/white in "
                        f"{attr_name}={val_stripped!r} on <{el.name}>"
                    )
                    if "currentcolor" in val_stripped.lower():
                        uses_theme_mechanism = True

                if aname == "style":
                    if "var(--" in val_stripped or "currentcolor" in val_stripped.lower():
                        uses_theme_mechanism = True
                    outside_var = _strip_var_calls(val_stripped)
                    assert not _PURE_BW_RE.search(outside_var), (
                        f"card {cid!r} <svg> uses un-themed hardcoded black/white in inline style "
                        f"{val_stripped!r} on <{el.name}>"
                    )

                if aname == "class":
                    cls_list = attr_val if isinstance(attr_val, list) else val_stripped.split()
                    if any(c in theme_styled_classes for c in cls_list):
                        uses_theme_mechanism = True

        for style_tag in svg.find_all("style"):
            stext = style_tag.get_text() or ""
            if "var(--" in stext or "currentcolor" in stext.lower():
                uses_theme_mechanism = True
            outside_var = _strip_var_calls(_strip_css_comments(stext))
            assert not _PURE_BW_RE.search(outside_var), (
                f"card {cid!r} <svg> <style> block contains un-themed hardcoded black/white"
            )

        assert uses_theme_mechanism, (
            f"card {cid!r} <svg> must style foreground/strokes/text/accents via CSS custom properties "
            f"(var(--...)) and/or currentColor"
        )


# --- 7. CSS grid and light/dark theme rules in styles.css ------------------


def test_styles_css_research_card_grid_and_dark_theme_rules():
    assert STYLES_CSS.is_file(), "styles.css missing"
    css_text = STYLES_CSS.read_text(encoding="utf8")
    blocks = _parse_css_blocks(css_text)

    grid_bodies = [
        body for selector, body in blocks
        if re.search(r"\.research-cards-grid\b", selector)
    ]
    assert grid_bodies, "styles.css missing CSS rule for .research-cards-grid"
    combined_grid_css = "\n".join(grid_bodies)
    assert re.search(r"\bdisplay\s*:\s*grid\b", combined_grid_css, re.I), (
        "styles.css .research-cards-grid rule must set display: grid"
    )
    assert re.search(r"\bgrid-template-columns\s*:", combined_grid_css, re.I), (
        "styles.css .research-cards-grid rule must set grid-template-columns"
    )

    root_vars = set()
    dark_vars = set()
    for selector, body in blocks:
        vars_in_block = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", body))
        if not vars_in_block:
            continue
        if re.search(r":root\b", selector):
            root_vars |= vars_in_block
        if re.search(r"body\.quarto-dark\b", selector):
            dark_vars |= vars_in_block

    assert root_vars, "styles.css must define light-mode CSS custom properties under :root"
    assert dark_vars, "styles.css must define dark-mode CSS custom properties under body.quarto-dark"
    assert root_vars & dark_vars, (
        "styles.css must define matching light (:root) and dark (body.quarto-dark) values for diagram/card CSS custom properties"
    )


# --- 8. Topic-page overview placement before legacy content ----------------


def test_topic_pages_overview_positioned_before_legacy_content(site):
    for sid, _ in EXPECTED_INTERESTS:
        slug_html = EXPECTED_TOPIC_LINKS[sid]
        page = site / "research" / slug_html
        assert page.is_file(), f"_site/research/{slug_html} missing"
        soup = soup_of(page)

        meta_desc = soup.find("meta", attrs={"name": "description"})
        assert meta_desc is not None and meta_desc.get("content", "").strip(), (
            f"research/{slug_html} missing meta description"
        )

        main = soup.find("main")
        assert main is not None, f"research/{slug_html} missing <main>"

        sec = main.select_one(f'section.research-interest[data-interest-id="{sid}"]')
        assert sec is not None, f"research/{slug_html} missing section.research-interest[data-interest-id={sid!r}]"

        pos = {id(node): idx for idx, node in enumerate(main.descendants)}
        sec_idx = pos[id(sec)]

        # Legacy prose paragraph (outside overview section, projects, publications)
        legacy_paras = [
            p for p in main.find_all("p")
            if p.find_parent("section", class_="research-interest") is None
            and p.find_parent("article", class_="project-entry") is None
            and p.find_parent("article", class_="pub-entry") is None
            and collapse_ws(p.get_text(" "))
        ]
        assert legacy_paras, f"research/{slug_html} lost its legacy prose paragraphs"
        assert sec_idx < pos[id(legacy_paras[0])], (
            f"section.research-interest on research/{slug_html} must appear before legacy prose paragraphs"
        )

        # Featured image(s)
        imgs = main.find_all("img")
        assert imgs, f"research/{slug_html} lost its featured <img> elements"
        assert sec_idx < pos[id(imgs[0])], (
            f"section.research-interest on research/{slug_html} must appear before featured <img>"
        )

        # Research projects
        projects = main.select("article.project-entry")
        assert projects, f"research/{slug_html} lost its article.project-entry elements"
        assert sec_idx < pos[id(projects[0])], (
            f"section.research-interest on research/{slug_html} must appear before ## Research projects"
        )

        # Publications
        pubs = main.select("article.pub-entry")
        assert pubs, f"research/{slug_html} lost its article.pub-entry elements"
        assert sec_idx < pos[id(pubs[0])], (
            f"section.research-interest on research/{slug_html} must appear before ## Publications"
        )


# --- 9. Data file data/research_interests.yml schema -----------------------


def test_research_interests_yml_schema_and_order():
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"
    data = yaml.safe_load(RESEARCH_INTERESTS_YML.read_text(encoding="utf8"))
    assert isinstance(data, list), "data/research_interests.yml must be a YAML list"
    assert len(data) == 5, f"expected 5 entries in data/research_interests.yml, got {len(data)}"

    bib_keys = {e["key"] for e in _parse_bib_entries()}
    actual_pairs = []
    primary_targets = []
    repo_resolved = REPO_ROOT.resolve()

    for entry in data:
        assert isinstance(entry, dict), f"interest entry must be a mapping: {entry!r}"
        iid = entry.get("id")
        title = entry.get("title")
        desc = entry.get("description")
        links = entry.get("links")
        papers = entry.get("papers")
        graphic = entry.get("graphic")

        assert isinstance(iid, str) and iid.strip(), f"invalid id: {iid!r}"
        assert isinstance(title, str) and title.strip(), f"invalid title for {iid!r}: {title!r}"
        assert isinstance(desc, str) and desc.strip(), f"empty description for {iid!r}"
        actual_pairs.append((iid, title))

        assert isinstance(graphic, str) and graphic.strip(), f"missing/empty graphic for {iid!r}: {graphic!r}"
        assert not Path(graphic).is_absolute(), f"graphic path for {iid!r} must be relative: {graphic!r}"
        assert graphic.lower().endswith(".svg"), f"graphic for {iid!r} must be an .svg file: {graphic!r}"
        graphic_path = (REPO_ROOT / graphic).resolve()
        assert repo_resolved in graphic_path.parents, f"graphic path for {iid!r} escapes repo root: {graphic!r}"
        assert graphic_path.is_file(), f"graphic file for {iid!r} does not exist: {graphic_path}"

        assert isinstance(links, list) and len(links) >= 1, f"invalid links for {iid!r}: {links!r}"
        for item in links:
            target = _extract_link_target(item)
            assert target in CANONICAL_CATEGORY_HTML, (
                f"interest {iid!r} has invalid link target {target!r} (allowed: {sorted(CANONICAL_CATEGORY_HTML)})"
            )
        primary_targets.append(_extract_link_target(links[0]))

        assert isinstance(papers, list) and 2 <= len(papers) <= 3, (
            f"interest {iid!r} must have 2 to 3 papers, got {papers!r}"
        )
        assert len(papers) == len(set(papers)), f"duplicate paper keys in {iid!r}: {papers!r}"
        for k in papers:
            assert k in bib_keys, f"interest {iid!r} references unknown BibTeX key {k!r}"

    assert actual_pairs == EXPECTED_INTERESTS, (
        f"data/research_interests.yml order/titles {actual_pairs!r} != expected {EXPECTED_INTERESTS!r}"
    )
    assert len(primary_targets) == len(set(primary_targets)), (
        f"duplicate primary link targets in data/research_interests.yml: {primary_targets!r}"
    )


# --- 10. Single-source dynamic rebuild (BibTeX & SVG) ----------------------


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

    topic_soup = soup_of(proj_dir / "_site" / "research" / "causalinf.html")
    item = topic_soup.select_one(
        'section.research-interest[data-interest-id="causal-inference"] '
        'li.highlighted-paper[data-key="SyntheticInterest2027"]'
    )
    assert item is not None, "SyntheticInterest2027 did not appear in causal-inference section on research/causalinf.html"
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

    # Must not appear on research/index.html cards
    index_soup = soup_of(proj_dir / "_site" / "research" / "index.html")
    assert index_soup.select_one('li.highlighted-paper[data-key="SyntheticInterest2027"]') is None


def test_dynamic_rebuild_editing_svg_graphic_updates_research_index(tmp_path):
    assert RESEARCH_INTERESTS_YML.is_file(), "data/research_interests.yml missing"

    proj_dir = tmp_path / "proj_svg_rebuild"
    _copy_project(proj_dir)

    yml_path = proj_dir / "data" / "research_interests.yml"
    interests = yaml.safe_load(yml_path.read_text(encoding="utf8"))
    graphic_rel = interests[0].get("graphic")
    assert isinstance(graphic_rel, str) and graphic_rel.strip(), "interests[0] missing graphic field"
    svg_path = proj_dir / graphic_rel
    assert svg_path.is_file(), f"graphic file missing: {svg_path}"

    marker = "Synthetic SVG Rebuild Marker 2027"
    orig_svg = svg_path.read_text(encoding="utf8")
    assert "</svg>" in orig_svg, f"closing </svg> tag not found in {svg_path}"
    updated_svg = orig_svg.replace("</svg>", f'<text x="10" y="10">{marker}</text></svg>', 1)
    svg_path.write_text(updated_svg, encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode == 0, f"quarto render failed:\n{proc.stderr[-2000:]}"

    index_soup = soup_of(proj_dir / "_site" / "research" / "index.html")
    svg = index_soup.select_one(
        'article.research-card[data-interest-id="causal-inference"] .research-card-graphic svg'
    )
    assert svg is not None, "causal-inference card inline <svg> missing on research/index.html"
    assert marker in collapse_ws(svg.get_text(" ")), (
        f"edited SVG marker {marker!r} did not propagate to research/index.html"
    )


# --- 11. Build validation on invalid data/research_interests.yml -----------


@pytest.mark.parametrize("mode", [
    "unknown_paper_key",
    "too_few_papers",
    "too_many_papers",
    "duplicate_id",
    "duplicate_paper_key",
    "empty_title",
    "empty_description",
    "invalid_link_target",
    "missing_graphic",
    "nonexistent_graphic",
    "invalid_svg",
    "unsafe_svg",
    "duplicate_link_mapping",
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
    elif mode == "missing_graphic":
        interests[0].pop("graphic", None)
    elif mode == "nonexistent_graphic":
        interests[0]["graphic"] = "research/graphics/does-not-exist.svg"
    elif mode == "invalid_svg":
        bad_svg = proj_dir / "research" / "graphics" / "broken.svg"
        bad_svg.parent.mkdir(parents=True, exist_ok=True)
        bad_svg.write_text("<svg viewBox='0 0 100 100'><rect></svg>", encoding="utf8")
        interests[0]["graphic"] = "research/graphics/broken.svg"
    elif mode == "unsafe_svg":
        unsafe_svg = proj_dir / "research" / "graphics" / "unsafe.svg"
        unsafe_svg.parent.mkdir(parents=True, exist_ok=True)
        unsafe_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<script>alert(1)</script><rect x="0" y="0" width="10" height="10"/></svg>',
            encoding="utf8",
        )
        interests[0]["graphic"] = "research/graphics/unsafe.svg"
    elif mode == "duplicate_link_mapping":
        target0 = _extract_link_target(interests[0]["links"][0])
        interests[1]["links"][0] = _replace_link_target(interests[1]["links"][0], target0)

    yml_path.write_text(yaml.safe_dump(interests, sort_keys=False, allow_unicode=True), encoding="utf8")

    proc = _run_quarto_render(proj_dir)
    assert proc.returncode != 0, (
        f"quarto render unexpectedly succeeded for invalid research_interests.yml mode {mode!r}"
    )
