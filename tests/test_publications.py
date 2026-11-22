"""Tests for issue #4: single-source bibliography, publications page, and public CV."""

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader

from site_helpers import (
    REPO_ROOT, RESEARCH_ORDER, collapse_ws, norm, redirects_to, resolve,
    run_publications_filter_js, sha256, soup_of,
)

REFERENCES_BIB = REPO_ROOT / "data" / "references.bib"
PUB_CATEGORIES_YML = REPO_ROOT / "data" / "publication_categories.yml"
LEGACY_BIB = REPO_ROOT / "data" / "legacy-publications.bib"
CV_PDF = REPO_ROOT / "files" / "CV_GuidoBiele.pdf"

ALLOWED_BIB_TYPES = {"article", "inproceedings", "techreport", "misc", "unpublished"}
PUBLISHED_TYPES = {"article", "inproceedings", "techreport"}
PREPRINT_TYPES = {"misc", "unpublished"}

DROPPED_DUPLICATE_KEYS = [
    "Leknes2013-wt",
    "Biele2022-iw",
    "Biele2008-ip",
    "Mohr2010-fo",
    "Biele2014-iu",
    "Suter2015-ch",
    "Biele2019-qm",
]
UNDATED_ABSTRACT_KEYS = [
    "Biele_undated-ue",
    "Mowinckel_undated-hi",
    "Ziegler_undated-iw",
]
RETRACTED_DOIS = [
    "10.1007/s00787-024-02508-5",
    "10.1007/s00787-026-03225-x",
    "10.1007/s00787-025-02690-0",
]

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


def parse_bib_with_keys(text):
    """Parse BibTeX entries top-level-first, preserving citation keys and types."""
    entries = []
    entry_re = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
    pos = 0
    while pos < len(text):
        m = entry_re.search(text, pos)
        if not m:
            break
        raw_type = m.group(1)
        kind = raw_type.lower()
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
        entries.append({"key": key, "type": kind, "raw_type": raw_type, "fields": fields})
    return entries


def latex_to_unicode(text):
    """Convert common BibTeX/LaTeX accents and escapes to Unicode."""
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


def latex_norm(text):
    """Normalize LaTeX/Unicode text into alphanumerics for comparison."""
    text = latex_to_unicode(text)
    text = re.sub(r"\\(?:leq|le|geq|ge|pm|times|cdot)\b", "", text)
    for cmd, ch, word in (
        ("mu", "\u03bc", "mu"), ("beta", "\u03b2", "beta"),
        ("alpha", "\u03b1", "alpha"), ("gamma", "\u03b3", "gamma"),
    ):
        text = re.sub(rf"\\{cmd}\b|{ch}", word, text)
    text = text.replace("\u00b5", "mu")
    text = re.sub(r"\\[A-Za-z]+\*?", "", text)
    return norm(text)


def pdf_norm(text):
    """Normalize PDF-extracted or LaTeX text, expanding ligatures and stripping accents."""
    text = latex_to_unicode(text)
    text = unicodedata.normalize("NFKC", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return norm(text)


def _load_bib():
    assert REFERENCES_BIB.is_file(), "data/references.bib missing"
    raw = REFERENCES_BIB.read_text(encoding="utf8")
    return raw, parse_bib_with_keys(raw)


def _find_entry(entries, *, key=None, doi=None, title_prefix=None, url=None):
    for e in entries:
        fields = e["fields"]
        if key and e["key"] == key:
            return e
        if doi and fields.get("doi", "").strip().casefold() == doi.casefold():
            return e
        if url and url in fields.get("url", ""):
            return e
        if title_prefix and latex_norm(fields.get("title", "")).startswith(latex_norm(title_prefix)):
            return e
    return None


def _first_author_family(author_field):
    first = latex_to_unicode(author_field.split(" and ")[0].strip())
    if "," in first:
        family = first.split(",", 1)[0].strip()
    else:
        family = first
    tokens = [t for t in family.split() if t]
    return tokens[-1] if tokens else ""


def _abstract_body_text(details_tag):
    summary = details_tag.find("summary")
    parts = []
    for child in details_tag.children:
        if child is summary:
            continue
        parts.append(child.get_text(" ") if hasattr(child, "get_text") else str(child))
    return collapse_ws(" ".join(parts))


def _pub_articles_by_key(site):
    page = site / "publications.html"
    assert page.is_file(), "_site/publications.html missing"
    soup = soup_of(page)
    return soup, {a.get("data-key", ""): a for a in soup.select("article.pub-entry")}


def _extract_pdf_details(pdf_path):
    reader = PdfReader(str(pdf_path))
    pages_text = [p.extract_text() or "" for p in reader.pages]
    full_text = "\n".join(pages_text)
    uris = []
    for page in reader.pages:
        for annot in (page.get("/Annots") or []):
            obj = annot.get_object()
            action = obj.get("/A")
            if action is not None:
                action_obj = action.get_object()
                if "/URI" in action_obj:
                    uris.append(str(action_obj["/URI"]))
    info_vals = []
    raw_info = reader.trailer.get("/Info")
    if raw_info is not None:
        info_obj = raw_info.get_object()
        info_vals.extend(f"{k}: {v}" for k, v in info_obj.items())
    if reader.metadata:
        info_vals.extend(f"{k}: {v}" for k, v in reader.metadata.items())
    return reader, pages_text, full_text, uris, info_vals


def _copy_project(dst):
    shutil.copytree(
        REPO_ROOT,
        dst,
        ignore=shutil.ignore_patterns("_site", ".quarto", ".git", ".pytest_cache", "__pycache__", ".issues"),
    )


def _render_project(project_dir):
    quarto = shutil.which("quarto")
    assert quarto is not None, "quarto executable not found on PATH"
    proc = subprocess.run(
        [quarto, "render"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        encoding="utf8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"quarto render failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    return project_dir / "_site"


# --- 1. Bibliography Single Source of Truth --------------------------------


def test_references_bib_exists_and_legacy_bib_removed():
    assert REFERENCES_BIB.is_file(), "data/references.bib must exist"
    assert not LEGACY_BIB.exists(), "data/legacy-publications.bib must be removed"


def test_bib_entry_counts_and_types():
    _, entries = _load_bib()
    assert len(entries) == 69
    bad_types = [(e["key"], e["type"]) for e in entries if e["type"] not in ALLOWED_BIB_TYPES]
    assert not bad_types, f"disallowed entry types: {bad_types}"
    published = [e for e in entries if e["type"] in PUBLISHED_TYPES]
    preprints = [e for e in entries if e["type"] == "misc"]
    assert len(published) == 67
    assert len(preprints) == 2
    assert {e["key"] for e in preprints} == {"Jones2020-ku", "Gerhardt2016-io"}


def test_bib_keys_unique_nonempty_and_no_whitespace():
    _, entries = _load_bib()
    keys = [e["key"] for e in entries]
    assert all(k and not any(ch.isspace() for ch in k) for k in keys), (
        f"empty or whitespace key(s): {[k for k in keys if not k or any(c.isspace() for c in k)]}"
    )
    assert len(keys) == len(set(keys)) == 69
    assert "VarnetPerez2025" in keys
    assert "Varnet Perez2025_Longterm" not in keys


def test_bib_required_fields_and_unique_titles():
    _, entries = _load_bib()
    norm_titles = []
    for e in entries:
        fields = e["fields"]
        assert fields.get("title", "").strip(), f"empty title in {e['key']}"
        assert fields.get("author", "").strip(), f"empty author in {e['key']}"
        year = fields.get("year", "").strip()
        assert re.fullmatch(r"\d{4}", year), f"invalid year {year!r} in {e['key']}"
        assert 2000 <= int(year) <= 2099, f"year out of range {year!r} in {e['key']}"
        nt = latex_norm(fields["title"])
        assert nt, f"normalized title empty for {e['key']}"
        norm_titles.append(nt)
    assert len(norm_titles) == len(set(norm_titles)) == 69


def test_bib_preserves_legacy_titles_and_abstracts(legacy):
    _, entries = _load_bib()
    by_title = {latex_norm(e["fields"]["title"]): e for e in entries}
    for pub in legacy["publications"]:
        nt = latex_norm(pub["title"])
        assert nt in by_title, f"legacy title missing from references.bib: {pub['pub_id']}"
        if pub["abstract_start"] is not None:
            bib_abs = latex_norm(by_title[nt]["fields"].get("abstract", ""))
            assert bib_abs.startswith(latex_norm(pub["abstract_start"])), (
                f"abstract mismatch for {pub['pub_id']}"
            )


def test_bib_includes_carried_over_missing_entries():
    _, entries = _load_bib()
    carried_dois = [
        ("10.1093/ije/dyac144", "2023", "What can we conclude about the effect of parental income on offspring mental health?"),
        ("10.1016/j.scitotenv.2021.147621", "2021", "Gestational blood levels of toxic metal and essential element mixtures"),
        ("10.1186/s13034-022-00547-x", "2023", "Developmental disorders among Norwegian-born children with immigrant parents"),
        ("10.1038/s41598-023-42412-7", "2023", None),
        ("10.3389/fneur.2023.1124943", "2023", None),
        ("10.1007/s00787-024-02374-1", "2024", None),
        ("10.1007/s00787-023-02284-8", "2024", None),
        ("10.1515/jci-2024-0009", "2025", "Recovery and inference of causal effects with sequential adjustment for confounding and attrition"),
        ("10.1007/s00127-024-02792-1", "2025", None),
        ("10.1177/10870547241301179", "2025", None),
        ("10.1186/s12889-025-21426-y", "2025", "Health among youth and young adults born in the nordic countries to immigrant parents"),
        ("10.1093/ije/dyaf010", "2025", None),
    ]
    for doi, year, title_prefix in carried_dois:
        e = _find_entry(entries, doi=doi)
        assert e is not None, f"missing DOI {doi}"
        assert e["fields"].get("year") == year, f"wrong year for {doi}"
        if title_prefix:
            assert latex_norm(e["fields"]["title"]).startswith(latex_norm(title_prefix)), doi

    varnet = _find_entry(entries, doi="10.1093/ije/dyaf010")
    assert varnet["key"] == "VarnetPerez2025"

    gerhardt = _find_entry(entries, key="Gerhardt2016-io")
    assert gerhardt is not None and gerhardt["type"] == "misc" and gerhardt["fields"].get("year") == "2016"
    assert latex_norm(gerhardt["fields"]["title"]) == latex_norm("Cognitive load increases risk aversion")

    jones = _find_entry(entries, key="Jones2020-ku")
    assert jones is not None and jones["type"] == "misc" and jones["fields"].get("year") == "2020"
    assert latex_norm(jones["fields"]["title"]) == latex_norm("An analysis of SARS-CoV-2 viral load by patient age")

    pmlr_entries = [
        ("The Probability of Tiered Benefit: Partial Identification with Robust and Stable Inference", "275", "2502.10049"),
        ("Causal Inference amid Missingness-Specific Independencies and Mechanism Shifts", "286", "2506.15441"),
    ]
    for title, vol, arxiv_id in pmlr_entries:
        e = _find_entry(entries, title_prefix=title)
        assert e is not None, f"missing entry: {title}"
        assert e["type"] == "inproceedings"
        assert e["fields"].get("year") == "2025"
        vol_haystack = " ".join(e["fields"].get(f, "") for f in ("volume", "booktitle", "series", "url", "pdf"))
        assert vol in vol_haystack and ("pmlr" in vol_haystack.lower() or "machine learning research" in vol_haystack.lower())
        eprint = e["fields"].get("eprint", "")
        archive_prefix = e["fields"].get("archiveprefix", "")
        assert arxiv_id in eprint
        assert "arxiv" in eprint.lower() or "arxiv" in archive_prefix.lower()


def test_bib_includes_new_2025_2026_additions():
    _, entries = _load_bib()
    new_works = [
        ("10.1080/08856257.2025.2478336", "2025", None),
        ("10.1007/s10903-025-01726-6", "2025", "Diagnoses of Mental Health Disorders Among Norwegian-Born Youth and Young Adults with Immigrant Parents—A Register-Based Study"),
        ("10.1186/s12889-025-24727-4", "2025", None),
        ("10.1017/S0033291726104954", "2026", None),
        ("10.1016/j.socscimed.2026.119571", "2026", None),
        ("10.1111/ejed.70903", "2026", None),
        ("10.1038/s44220-026-00700-x", "2026", None),
    ]
    for doi, year, title in new_works:
        e = _find_entry(entries, doi=doi)
        assert e is not None, f"missing 2025-2026 DOI {doi}"
        assert e["fields"].get("year") == year
        if title:
            assert latex_norm(e["fields"]["title"]) == latex_norm(title)

    fhi = _find_entry(entries, url="https://hdl.handle.net/11250/5528884")
    assert fhi is not None, "missing 2026 FHI report"
    assert fhi["type"] == "techreport"
    assert fhi["fields"].get("year") == "2026"
    assert latex_norm(fhi["fields"]["title"]) == latex_norm(
        "Kjennetegn ved barn som vokser opp i fattigdom: oppvekstsvilkår, helse- og skoleforhold"
    )


def test_bib_excludes_duplicates_undated_and_retracted():
    raw, entries = _load_bib()
    keys = {e["key"] for e in entries}
    for k in DROPPED_DUPLICATE_KEYS + UNDATED_ABSTRACT_KEYS + ["Nyquist2025-Populationadjusted"]:
        assert k not in keys, f"excluded key still present: {k}"
    dois = {e["fields"].get("doi", "").strip().casefold() for e in entries}
    for doi in RETRACTED_DOIS:
        assert doi.casefold() not in dois, f"retracted/reply DOI present: {doi}"
    lower_raw = raw.casefold()
    for term in ("nyquist", "gender incongruence", "banos"):
        assert term not in lower_raw, f"retracted term {term!r} found in data/references.bib"


def test_bib_has_no_known_typos():
    raw, entries = _load_bib()
    for typo in ("Gudio", "Frigess,i", "Norwegain", "Bransæter", r"Brans{\ae}ter"):
        assert typo not in raw, f"typo {typo!r} found in data/references.bib"
    assert "to appear" not in raw
    for e in entries:
        for field, val in e["fields"].items():
            if field != "abstract":
                assert "to appear" not in val.casefold(), f"'to appear' in {e['key']}.{field}"


# --- 2. Rendered Publications Page -----------------------------------------


def test_publications_page_has_no_legacy_csl_markup(site):
    page = site / "publications.html"
    assert page.is_file()
    raw_html = page.read_text(encoding="utf8", errors="replace")
    assert "This list of publications is being rebuilt" not in raw_html
    assert soup_of(page).select("div.csl-entry") == []


def test_publications_page_separates_published_and_preprints(site):
    _, entries = _load_bib()
    bib_by_key = {e["key"]: e for e in entries}
    soup, _ = _pub_articles_by_key(site)

    pub_sec = soup.select_one("#published-section #published")
    pre_sec = soup.select_one("#preprints-section #preprints")
    assert pub_sec is not None, "#published-section #published missing"
    assert pre_sec is not None, "#preprints-section #preprints missing"

    pub_articles = pub_sec.select("article.pub-entry")
    pre_articles = pre_sec.select("article.pub-entry")
    all_articles = soup.select("article.pub-entry")
    assert len(pub_articles) == 67
    assert len(pre_articles) == 2
    assert len(all_articles) == 69

    for art in pub_articles:
        assert art.get("data-pub-type") in PUBLISHED_TYPES
    for art in pre_articles:
        assert art.get("data-pub-type") == "preprint"

    seen_keys = set()
    for art in all_articles:
        key = art.get("data-key", "")
        year = art.get("data-year", "")
        pub_type = art.get("data-pub-type", "")
        assert key in bib_by_key, f"unexpected data-key {key!r}"
        assert key not in seen_keys, f"duplicate data-key {key!r}"
        seen_keys.add(key)
        bib_entry = bib_by_key[key]
        assert year == bib_entry["fields"]["year"]
        expected_type = "preprint" if bib_entry["type"] in PREPRINT_TYPES else bib_entry["type"]
        assert pub_type == expected_type


def test_preprint_badge_placement(site):
    soup, _ = _pub_articles_by_key(site)
    pub_sec = soup.select_one("#published-section #published")
    pre_sec = soup.select_one("#preprints-section #preprints")
    assert pub_sec is not None and pre_sec is not None

    for art in pub_sec.select("article.pub-entry"):
        assert art.select(".pub-badge-preprint") == []
        title = art.select_one(".pub-title")
        assert title is not None
        assert title.select('[class*="badge"]') == []

    for art in pre_sec.select("article.pub-entry"):
        badges = art.select("span.pub-badge-preprint")
        assert len(badges) == 1
        assert badges[0].get_text(strip=True) == "Preprint"
        title = art.select_one(".pub-title")
        assert title is not None
        assert badges[0] not in title.descendants
        assert not title.get_text(" ", strip=True).startswith("Preprint")


def test_publications_grouped_by_descending_year(site):
    soup, _ = _pub_articles_by_key(site)

    for container_sel, expected_top, expected_bottom in (
        ("#published", 2026, 2007),
        ("#preprints", 2020, 2016),
    ):
        container = soup.select_one(container_sel)
        assert container is not None, f"{container_sel} missing"
        sections = container.select("section.pub-year")
        assert sections, f"no section.pub-year inside {container_sel}"
        years = []
        for sec in sections:
            y_str = sec.get("data-year", "")
            assert re.fullmatch(r"\d{4}", y_str), f"bad section data-year {y_str!r}"
            years.append(int(y_str))
            heading = next(
                (h for h in sec.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
                 if h.find_parent("article") is None),
                None,
            )
            assert heading is not None and y_str in heading.get_text(), (
                f"missing year heading for {y_str} in {container_sel}"
            )
            for art in sec.select("article.pub-entry"):
                assert art.get("data-year") == y_str
        assert years == sorted(years, reverse=True)
        assert len(years) == len(set(years))
        assert years[0] == expected_top and years[-1] == expected_bottom

    for art in soup.select("article.pub-entry"):
        sec = art.find_parent("section", class_="pub-year")
        assert sec is not None and sec.get("data-year") == art.get("data-year")


def test_rendered_entries_clean_unicode_and_no_raw_latex(site):
    _, bib_entries = _load_bib()
    soup, by_key = _pub_articles_by_key(site)
    assert len(by_key) == 69

    bad_tex_re = re.compile(r"\\(?:[A-Za-z]+|[&%$#_{}~^'\"`])")
    bad_math_re = re.compile(r"\$(?:\\[A-Za-z]+|[A-Za-z][ _^=<>+\-*/\\A-Za-z0-9]{0,20})\$")
    bad_entity_re = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")

    for key, art in by_key.items():
        for sel in (".pub-title", ".pub-authors", ".pub-venue", "details.pub-abstract"):
            el = art.select_one(sel)
            if el is None:
                continue
            txt = el.get_text(" ")
            assert "{" not in txt and "}" not in txt, f"raw braces in {key} {sel}: {txt!r}"
            assert not bad_tex_re.search(txt), f"raw LaTeX in {key} {sel}: {txt!r}"
            assert not bad_math_re.search(txt), f"raw TeX math in {key} {sel}: {txt!r}"
            assert not bad_entity_re.search(txt), f"double-escaped entity in {key} {sel}: {txt!r}"

    page_authors = " | ".join(el.get_text(" ") for el in soup.select(".pub-authors"))
    page_venues = " | ".join(el.get_text(" ") for el in soup.select(".pub-venue"))
    page_titles = " | ".join(el.get_text(" ") for el in soup.select(".pub-title"))
    for name in ("Tomás Varnet Pérez", "Kristin Romvig Øvergaard", "Pål Surén", "Anne Lise Brantsæter", "Silje Kvam Bårdstu"):
        assert name in collapse_ws(page_authors), f"missing rendered author {name!r}"
    for venue in ("Social Science & Medicine", "Psychonomic Bulletin & Review"):
        assert venue in collapse_ws(page_venues), f"missing rendered venue {venue!r}"
    assert "oppvekstsvilkår" in collapse_ws(page_titles)

    jore_bib = _find_entry(bib_entries, doi="10.1016/j.jpsychores.2020.110175")
    assert "β" in by_key[jore_bib["key"]].select_one("details.pub-abstract").get_text(" ")

    clear_bib = _find_entry(
        bib_entries,
        title_prefix="The Probability of Tiered Benefit: Partial Identification with Robust and Stable Inference",
    )
    clear_abs = collapse_ws(by_key[clear_bib["key"]].select_one("details.pub-abstract").get_text(" "))
    assert "K ≥ 2" in clear_abs


def test_rendered_entries_highlight_only_guido_biele(site):
    _, by_key = _pub_articles_by_key(site)
    assert len(by_key) == 69
    for key, art in by_key.items():
        authors = art.select_one(".pub-authors")
        assert authors is not None, f"missing .pub-authors in {key}"
        selves = authors.select("strong.pub-author-self")
        assert len(selves) == 1, f"expected exactly 1 .pub-author-self in {key}, got {len(selves)}"
        self_text = collapse_ws(selves[0].get_text())
        lower = self_text.casefold()
        assert "biele" in lower and re.search(r"\bguido\b|\bg\.?(?=\s|$)", lower), (
            f"unexpected .pub-author-self text in {key}: {self_text!r}"
        )
        assert " and " not in lower and len(self_text.split()) <= 5, (
            f"co-authors leaked into .pub-author-self in {key}: {self_text!r}"
        )


def test_rendered_entries_have_venues_and_links(site):
    _, bib_entries = _load_bib()
    _, by_key = _pub_articles_by_key(site)
    pub_page = site / "publications.html"

    for e in bib_entries:
        key = e["key"]
        fields = e["fields"]
        art = by_key[key]
        venue = art.select_one(".pub-venue")
        assert venue is not None and venue.get_text(strip=True), f"empty .pub-venue for {key}"

        links = art.select(".pub-links a[href]")
        if fields.get("doi"):
            expected_doi = f"https://doi.org/{fields['doi'].strip().casefold()}"
            assert any(
                "DOI" in a.get_text() and a["href"].strip().casefold() == expected_doi
                for a in links
            ), f"missing DOI link for {key}"

        pdf_val = fields.get("pdf") or fields.get("url_pdf")
        if pdf_val:
            assert any("PDF" in a.get_text() and pdf_val.strip() in a["href"] for a in links), (
                f"missing PDF link for {key}"
            )

        preprint_val = fields.get("eprint") or fields.get("url_preprint")
        if preprint_val:
            assert any("Preprint" in a.get_text() for a in links), f"missing Preprint link for {key}"

        code_val = fields.get("code") or fields.get("url_code")
        if code_val:
            assert any("Code" in a.get_text() and code_val.strip() in a["href"] for a in links), (
                f"missing Code link for {key}"
            )

        url_val = fields.get("url", "").strip()
        if url_val and not fields.get("doi") and "doi.org/" not in url_val.lower():
            assert any(url_val in a["href"] for a in links), f"missing non-DOI URL link for {key}"

    biele2019 = _find_entry(bib_entries, doi="10.1007/s10654-019-00550-1")
    biele2019_pdfs = [a for a in by_key[biele2019["key"]].select(".pub-links a[href]") if "PDF" in a.get_text()]
    assert biele2019_pdfs and "files/Biele_2019.pdf" in biele2019_pdfs[0]["href"]
    assert resolve(site, pub_page, biele2019_pdfs[0]["href"]).is_file()

    clear_e = _find_entry(
        bib_entries,
        title_prefix="The Probability of Tiered Benefit: Partial Identification with Robust and Stable Inference",
    )
    clear_links = by_key[clear_e["key"]].select(".pub-links a[href]")
    assert any(
        "PDF" in a.get_text()
        and a["href"].strip() == "https://raw.githubusercontent.com/mlresearch/v275/main/assets/aguas25a/aguas25a.pdf"
        for a in clear_links
    )
    assert any(
        "Proceedings" in a.get_text()
        and a["href"].strip() == "https://proceedings.mlr.press/v275/aguas25a.html"
        for a in clear_links
    )
    assert any("Preprint" in a.get_text() and "https://arxiv.org/abs/2502.10049" in a["href"] for a in clear_links)

    uai_e = _find_entry(
        bib_entries,
        title_prefix="Causal Inference amid Missingness-Specific Independencies and Mechanism Shifts",
    )
    uai_links = by_key[uai_e["key"]].select(".pub-links a[href]")
    assert any(
        "PDF" in a.get_text()
        and a["href"].strip() == "https://raw.githubusercontent.com/mlresearch/v286/main/assets/aguas25a/aguas25a.pdf"
        for a in uai_links
    )
    assert any(
        "Proceedings" in a.get_text()
        and a["href"].strip() == "https://proceedings.mlr.press/v286/aguas25a.html"
        for a in uai_links
    )
    assert any("Preprint" in a.get_text() and "https://arxiv.org/abs/2506.15441" in a["href"] for a in uai_links)

    gerhardt_links = by_key["Gerhardt2016-io"].select(".pub-links a[href]")
    assert any("PDF" in a.get_text() and "econstor" in a["href"].lower() for a in gerhardt_links)

    jones_links = by_key["Jones2020-ku"].select(".pub-links a[href]")
    assert any("Preprint" in a.get_text() and "medrxiv" in a["href"].lower() for a in jones_links)

    fhi_e = _find_entry(bib_entries, url="https://hdl.handle.net/11250/5528884")
    fhi_links = by_key[fhi_e["key"]].select(".pub-links a[href]")
    assert any("https://hdl.handle.net/11250/5528884" in a["href"] for a in fhi_links)


def test_rendered_entries_abstracts_match_bib(site, legacy):
    _, bib_entries = _load_bib()
    _, by_key = _pub_articles_by_key(site)

    for e in bib_entries:
        key = e["key"]
        bib_abs = e["fields"].get("abstract", "").strip()
        details = by_key[key].select_one("details.pub-abstract")
        if bib_abs:
            assert details is not None, f"missing <details class='pub-abstract'> for {key}"
            summary = details.find("summary")
            assert summary is not None and collapse_ws(summary.get_text()) == "Abstract"
            body = _abstract_body_text(details)
            assert latex_norm(body) == latex_norm(bib_abs), f"rendered abstract mismatch for {key}"
        else:
            assert details is None, f"unexpected empty/orphan abstract block for {key}"

    by_title = {norm(art.select_one(".pub-title").get_text(" ")): art for art in by_key.values()}
    for pub in legacy["publications"]:
        if pub["abstract_start"] is None:
            continue
        art = by_title[norm(pub["title"])]
        body = _abstract_body_text(art.select_one("details.pub-abstract"))
        assert latex_norm(body).startswith(latex_norm(pub["abstract_start"])), pub["pub_id"]


def test_publications_category_filter_bar_and_badges(site):
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"
    cat_map = yaml.safe_load(PUB_CATEGORIES_YML.read_text(encoding="utf8"))
    assert isinstance(cat_map, dict)

    page = site / "publications.html"
    soup, by_key = _pub_articles_by_key(site)
    assert len(by_key) == 69

    filter_bar = soup.select_one(".pub-category-filter")
    pub_sec = soup.select_one("#published-section")
    assert filter_bar is not None, "missing .pub-category-filter on publications.html"
    assert pub_sec is not None, "missing #published-section on publications.html"
    # Filter bar must appear above #published-section in document order
    all_tags = list(soup.find_all(True))
    assert all_tags.index(filter_bar) < all_tags.index(pub_sec), (
        ".pub-category-filter must appear before #published-section"
    )

    buttons = filter_bar.select("button.pub-filter-btn[data-category]")
    btn_cats = [b.get("data-category") for b in buttons]
    assert btn_cats == ["all"] + RESEARCH_ORDER, f"unexpected filter buttons: {btn_cats!r}"
    assert collapse_ws(buttons[0].get_text()) == "All"
    assert buttons[0].get("aria-pressed") == "true"
    for b in buttons[1:]:
        assert b.get("aria-pressed") == "false", (
            f"category button {b.get('data-category')!r} should default to aria-pressed='false'"
        )

    for key, art in by_key.items():
        expected_cats = [c for c in RESEARCH_ORDER if c in (cat_map.get(key) or [])]
        assert expected_cats, f"no categories in data/publication_categories.yml for {key}"
        actual_attr = (art.get("data-categories") or "").strip().split()
        assert actual_attr == expected_cats, (
            f"{key}: expected data-categories {expected_cats!r}, got {actual_attr!r}"
        )

        badges = art.select(".pub-categories a.pub-category-badge[href]")
        assert len(badges) == len(expected_cats), (
            f"{key}: expected {len(expected_cats)} category badges, got {len(badges)}"
        )
        for badge, slug in zip(badges, expected_cats):
            target = resolve(site, page, badge["href"]).resolve()
            expected_target = (site / "research" / f"{slug}.html").resolve()
            assert target == expected_target and expected_target.is_file(), (
                f"{key}: badge href {badge['href']!r} resolved to {target}, expected {expected_target}"
            )


def test_publications_client_side_category_filter_runtime(site):
    assert PUB_CATEGORIES_YML.is_file(), "data/publication_categories.yml missing"
    cat_map = yaml.safe_load(PUB_CATEGORIES_YML.read_text(encoding="utf8"))

    page = site / "publications.html"
    res = run_publications_filter_js(
        page,
        query="",
        actions=[
            {"type": "click", "category": "adhd", "label": "adhd"},
            {"type": "click", "category": "causalinf", "label": "causalinf"},
            {"type": "click", "category": "all", "label": "all"},
        ],
    )
    assert res["found"], "no inline category filter <script> found in _site/publications.html"

    # Initial state: all 69 visible, All active
    init = res["initial"]
    assert len(init["entries"]) == 69
    assert all(e["visible"] for e in init["entries"]), "all 69 entries must be visible by default"
    assert init["publishedSectionVisible"] is True
    assert init["preprintsSectionVisible"] is True
    all_btn = next(b for b in init["buttons"] if b["category"] == "all")
    assert all_btn["ariaPressed"] == "true"

    # Clicking 'adhd' and 'causalinf'
    for slug in ("adhd", "causalinf"):
        snap = res["actions"][slug]
        expected_keys = {k for k, cats in cat_map.items() if slug in cats}
        assert expected_keys, f"no papers in category {slug!r}"
        visible_keys = {e["key"] for e in snap["entries"] if e["visible"]}
        assert visible_keys == expected_keys, (
            f"filter {slug!r}: visible keys mismatch (extra={visible_keys - expected_keys}, "
            f"missing={expected_keys - visible_keys})"
        )
        for btn in snap["buttons"]:
            if btn["category"] == slug:
                assert btn["ariaPressed"] == "true" and btn["activeClass"], (
                    f"button {slug!r} not marked active/aria-pressed=true: {btn}"
                )
            else:
                assert btn["ariaPressed"] == "false" and not btn["activeClass"], (
                    f"non-selected button {btn['category']!r} still active: {btn}"
                )
        # Empty year sections must be hidden; non-empty visible
        visible_by_sec = {}
        for e in snap["entries"]:
            if e["visible"]:
                visible_by_sec.setdefault(e["key"], True)
        soup = soup_of(page)
        for yr in snap["years"]:
            sec_el = soup.select_one(f'#{yr["parentId"]} section.pub-year[data-year="{yr["year"]}"]')
            sec_keys = [a.get("data-key") for a in sec_el.select("article.pub-entry")]
            has_any = any(k in visible_keys for k in sec_keys)
            assert yr["visible"] is has_any, (
                f"filter {slug!r}: #{yr['parentId']} year {yr['year']} visible={yr['visible']}, expected {has_any}"
            )
        pub_keys = {a.get("data-key") for a in soup.select("#published article.pub-entry")}
        pre_keys = {a.get("data-key") for a in soup.select("#preprints article.pub-entry")}
        assert snap["publishedSectionVisible"] is bool(pub_keys & visible_keys)
        assert snap["preprintsSectionVisible"] is bool(pre_keys & visible_keys)

    # Clicking 'all' restores everything
    restored = res["actions"]["all"]
    assert all(e["visible"] for e in restored["entries"])
    assert all(y["visible"] for y in restored["years"])
    assert restored["publishedSectionVisible"] is True
    assert restored["preprintsSectionVisible"] is True

    # Query string ?category=bayes applies filter on initial load
    qres = run_publications_filter_js(page, query="?category=bayes")
    assert qres["found"]
    qinit = qres["initial"]
    expected_bayes = {k for k, cats in cat_map.items() if "bayes" in cats}
    visible_bayes = {e["key"] for e in qinit["entries"] if e["visible"]}
    assert visible_bayes == expected_bayes
    bayes_btn = next(b for b in qinit["buttons"] if b["category"] == "bayes")
    assert bayes_btn["ariaPressed"] == "true" and bayes_btn["activeClass"]


def test_legacy_publication_aliases_and_one_to_one_titles(site, legacy):
    index_redirect = site / "publication" / "index.html"
    assert index_redirect.is_file()
    assert redirects_to(site, index_redirect, site / "publications.html")

    for pub in legacy["publications"]:
        old = site / "publication" / pub["pub_id"] / "index.html"
        assert old.is_file(), f"missing redirect page for {pub['pub_id']}"
        assert redirects_to(site, old, site / "publications.html")

    _, by_key = _pub_articles_by_key(site)
    assert len(by_key) == 69
    rendered_titles = [norm(art.select_one(".pub-title").get_text(" ")) for art in by_key.values()]
    for pub in legacy["publications"]:
        assert rendered_titles.count(norm(pub["title"])) == 1, pub["pub_id"]


def test_dynamic_bib_rebuild_updates_publications_page(tmp_path):
    assert REFERENCES_BIB.is_file(), "data/references.bib missing"
    proj = tmp_path / "proj"
    _copy_project(proj)

    extra_bib = """

@article{SyntheticTest2027a,
  title = {Synthetic Dynamic Rebuild Article for Testing},
  author = {Biele, G. P. and Doe, Jane},
  journal = {Journal of Testing \\& Reproducibility},
  year = {2027},
  doi = {10.5555/synthetic.2027.1},
  pdf = {https://example.org/synthetic2027a.pdf},
  code = {https://github.com/gbiele/synthetic2027a},
  url_preprint = {https://arxiv.org/abs/2701.99999},
  abstract = {Synthetic dynamic rebuild abstract verifying single-source generation.}
}

@unpublished{SyntheticPreprint2027b,
  title = {Synthetic Working Paper for Dynamic Rebuild},
  author = {Guido Biele and Jane Doe},
  journal = {OSF Preprints},
  year = {2027},
  eprint = {arXiv:2702.88888}
}
"""
    bib_path = proj / "data" / "references.bib"
    bib_path.write_text(bib_path.read_text(encoding="utf8") + extra_bib, encoding="utf8")

    out_site = _render_project(proj)
    soup = soup_of(out_site / "publications.html")
    all_entries = soup.select("article.pub-entry")
    assert len(all_entries) == 71

    pub_sec = soup.select_one("#published")
    pre_sec = soup.select_one("#preprints")
    assert len(pub_sec.select("article.pub-entry")) == 68
    assert len(pre_sec.select("article.pub-entry")) == 3

    art_year = pub_sec.select_one('section.pub-year[data-year="2027"]')
    assert art_year is not None
    art = art_year.select_one('article.pub-entry[data-key="SyntheticTest2027a"]')
    assert art is not None and art.get("data-pub-type") == "article"
    assert art.select_one("strong.pub-author-self").get_text(strip=True) == "G. P. Biele"
    assert "Journal of Testing & Reproducibility" in collapse_ws(art.select_one(".pub-venue").get_text(" "))
    links = art.select(".pub-links a[href]")
    assert any("DOI" in a.get_text() and "10.5555/synthetic.2027.1" in a["href"] for a in links)
    assert any("PDF" in a.get_text() and "synthetic2027a.pdf" in a["href"] for a in links)
    assert any("Preprint" in a.get_text() and "2701.99999" in a["href"] for a in links)
    assert any("Code" in a.get_text() and "github.com/gbiele/synthetic2027a" in a["href"] for a in links)
    assert "Synthetic dynamic rebuild abstract" in art.select_one("details.pub-abstract").get_text(" ")

    pre_year = pre_sec.select_one('section.pub-year[data-year="2027"]')
    assert pre_year is not None
    pre = pre_year.select_one('article.pub-entry[data-key="SyntheticPreprint2027b"]')
    assert pre is not None and pre.get("data-pub-type") == "preprint"
    badge = pre.select_one("span.pub-badge-preprint")
    assert badge is not None and badge.get_text(strip=True) == "Preprint"
    assert badge not in pre.select_one(".pub-title").descendants
    assert pre.select_one("strong.pub-author-self").get_text(strip=True) == "Guido Biele"
    assert any("Preprint" in a.get_text() and "2702.88888" in a["href"] for a in pre.select(".pub-links a[href]"))


def test_quarto_render_leaves_git_clean(tmp_path):
    ls_out = subprocess.run(
        ["git", "ls-files", "_site", ".quarto"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf8", errors="replace",
    )
    assert ls_out.returncode == 0 and ls_out.stdout.strip() == ""
    ig_out = subprocess.run(
        ["git", "check-ignore", "_site", ".quarto"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf8", errors="replace",
    )
    assert ig_out.returncode == 0

    proj = tmp_path / "gitproj"
    _copy_project(proj)
    for cmd in (
        ["git", "init"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "snapshot"],
    ):
        proc = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, encoding="utf8", errors="replace")
        assert proc.returncode == 0, proc.stderr

    _render_project(proj)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=proj, capture_output=True, text=True, encoding="utf8", errors="replace",
    )
    assert status.returncode == 0
    assert status.stdout.strip() == "", f"quarto render left dirty/untracked files:\n{status.stdout}"


# --- 3. Public CV PDF ------------------------------------------------------


def test_cv_pdf_exists_copied_and_linked_in_nav_and_about(site, legacy):
    assert CV_PDF.is_file(), "files/CV_GuidoBiele.pdf missing"
    site_cv = site / "files" / "CV_GuidoBiele.pdf"
    assert site_cv.is_file(), "_site/files/CV_GuidoBiele.pdf missing"
    assert sha256(CV_PDF) != legacy["cv_sha256"], "CV PDF is still the old file"
    assert sha256(site_cv) == sha256(CV_PDF)

    reader = PdfReader(str(CV_PDF))
    assert len(reader.pages) >= 1

    about = site / "index.html"
    about_soup = soup_of(about)
    main = about_soup.find("main") or about_soup.body or about_soup
    assert any(
        resolve(site, about, a["href"]).resolve() == site_cv.resolve()
        for a in main.find_all("a", href=True)
    ), "index.html main content does not link to files/CV_GuidoBiele.pdf"

    for page_name in ("index.html", "publications.html"):
        page = site / page_name
        nav = soup_of(page).find("nav")
        assert nav is not None
        assert any(
            resolve(site, page, a["href"]).resolve() == site_cv.resolve()
            for a in nav.find_all("a", href=True)
        ), f"navbar on {page_name} does not link to files/CV_GuidoBiele.pdf"


def test_cv_pdf_redacts_phone_and_email():
    assert CV_PDF.is_file(), "files/CV_GuidoBiele.pdf missing"
    _, _, full_text, uris, info_vals = _extract_pdf_details(CV_PDF)
    combined = "\n".join([full_text, *uris, *info_vals])
    digits = "".join(ch for ch in combined if ch.isdigit())

    assert "21078350" not in digits, "phone digits leaked in CV PDF"
    assert "+47" not in combined, "country code +47 leaked in CV PDF"
    assert not re.search(r"(?:Phone|\bphone\s*:)", combined), "Phone label leaked in CV PDF"
    assert "guido.biele@fhi.no" not in combined.casefold(), "email address leaked in CV PDF"
    assert "mailto:" not in combined.casefold(), "mailto: link leaked in CV PDF"
    assert not re.search(r"(?:Email:|\bemail\s*:)", combined), "Email label leaked in CV PDF"


def test_cv_pdf_retains_office_address_and_google_scholar():
    assert CV_PDF.is_file(), "files/CV_GuidoBiele.pdf missing"
    _, _, full_text, uris, _ = _extract_pdf_details(CV_PDF)
    ws_text = collapse_ws(full_text)
    for expected in (
        "Norwegian Institute of Public Health",
        "Marcus Thranes gate 6",
        "0473 Oslo",
        "Google Scholar",
    ):
        assert expected in ws_text, f"missing retained CV header item {expected!r}"
    assert any(
        any(tok in uri.lower() for tok in ("scholar.google", "tinyurl.com/gubipubs", "tinyurl.com/gbpubsgs"))
        for uri in uris
    ), f"missing Google Scholar /URI annotation in {uris!r}"


def test_cv_pdf_synced_with_bib_and_excludes_removed_works():
    _, bib_entries = _load_bib()
    assert CV_PDF.is_file(), "files/CV_GuidoBiele.pdf missing"
    _, _, full_text, _, _ = _extract_pdf_details(CV_PDF)
    ws_text = collapse_ws(full_text)
    lower_ws = ws_text.casefold()

    for marker in ("Varnet Perez2025_Longterm", "(?", "(?, ?)", "\\bibentry"):
        assert marker not in ws_text, f"undefined citation marker {marker!r} in CV PDF"

    for excluded in (
        "gender incongruence",
        "nyquist",
        "banos",
        "cpt performance predicts medication response",
        "methylphenidate on brain processes",
        "discriminates medication persisters",
    ):
        assert excluded not in lower_ws, f"excluded item {excluded!r} found in CV PDF"

    cv_norm = pdf_norm(full_text)
    missing = []
    for e in bib_entries:
        nt = pdf_norm(e["fields"]["title"])
        fam = pdf_norm(_first_author_family(e["fields"]["author"]))
        if nt not in cv_norm or (fam and fam not in cv_norm):
            missing.append(e["key"])
    assert not missing, f"CV PDF missing {len(missing)} bib entries, e.g. {missing[:10]}"
