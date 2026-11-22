"""Held-out tests for issue #4: same contract, independent inputs and edges."""

import re
import shutil
import subprocess
import unicodedata

from pypdf import PdfReader

from site_helpers import REPO_ROOT, collapse_ws, norm, soup_of

REFERENCES_BIB = REPO_ROOT / "data" / "references.bib"
CV_PDF = REPO_ROOT / "files" / "CV_GuidoBiele.pdf"

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


def _parse_bib(text):
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


def _pdf_norm(text):
    text = _latex_to_unicode(text)
    text = unicodedata.normalize("NFKC", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return norm(text)


def _iter_action_uris(action_obj):
    if action_obj is None:
        return
    obj = action_obj.get_object()
    if "/URI" in obj:
        yield str(obj["/URI"])
    nxt = obj.get("/Next")
    if nxt is not None:
        nxt_obj = nxt.get_object()
        items = nxt_obj if isinstance(nxt_obj, list) else [nxt_obj]
        for item in items:
            yield from _iter_action_uris(item)


def test_heldout_dynamic_rebuild_variants(tmp_path):
    assert REFERENCES_BIB.is_file(), "data/references.bib missing"
    proj = tmp_path / "proj"
    shutil.copytree(
        REPO_ROOT,
        proj,
        ignore=shutil.ignore_patterns("_site", ".quarto", ".git", ".pytest_cache", "__pycache__", ".issues"),
    )

    addition = """

@inproceedings{HeldoutConf2028,
  title = {Heldout Conference Study on {Causal} Effects},
  author = {Doe, Jane and Biele, Guido Philipp Emmanuel},
  booktitle = {Proceedings of Causal \\& Observational Methods},
  year = {2028},
  doi = {10.5555/heldout.conf.2028},
  url_pdf = {https://example.org/heldout2028.pdf},
  url_code = {https://github.com/gbiele/heldout2028},
  eprint = {2801.01234},
  archivePrefix = {arXiv},
  abstract = {The estimated parameter was $\\gamma$ = 0.42 in {Norway}.}
}

@misc{HeldoutPreprint2028,
  title = {Working paper on {\\o}konomi and {\\aa}rsaker},
  author = {Guido P. Biele and Kari Nordmann},
  howpublished = {OSF Preprints},
  year = {2028},
  url_preprint = {https://osf.io/preprints/heldout2028}
}
"""
    bib_path = proj / "data" / "references.bib"
    bib_path.write_text(bib_path.read_text(encoding="utf8") + addition, encoding="utf8")

    quarto = shutil.which("quarto")
    assert quarto is not None
    proc = subprocess.run(
        [quarto, "render"], cwd=proj, capture_output=True, text=True,
        encoding="utf8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]

    soup = soup_of(proj / "_site" / "publications.html")
    assert len(soup.select("article.pub-entry")) == 71

    pub_years = [int(s["data-year"]) for s in soup.select("#published section.pub-year")]
    pre_years = [int(s["data-year"]) for s in soup.select("#preprints section.pub-year")]
    assert pub_years[0] == 2028 and pub_years == sorted(pub_years, reverse=True)
    assert pre_years[0] == 2028 and pre_years == sorted(pre_years, reverse=True)

    conf = soup.select_one('#published section.pub-year[data-year="2028"] article[data-key="HeldoutConf2028"]')
    assert conf is not None and conf.get("data-pub-type") == "inproceedings"
    assert conf.select_one("strong.pub-author-self").get_text(strip=True) == "Guido Philipp Emmanuel Biele"
    assert "Jane Doe" in collapse_ws(conf.select_one(".pub-authors").get_text(" "))
    assert "Proceedings of Causal & Observational Methods" in collapse_ws(conf.select_one(".pub-venue").get_text(" "))
    conf_links = conf.select(".pub-links a[href]")
    assert any("DOI" in a.get_text() and "10.5555/heldout.conf.2028" in a["href"] for a in conf_links)
    assert any("PDF" in a.get_text() and "https://example.org/heldout2028.pdf" in a["href"] for a in conf_links)
    assert any("Preprint" in a.get_text() and "https://arxiv.org/abs/2801.01234" in a["href"] for a in conf_links)
    assert any("Code" in a.get_text() and "https://github.com/gbiele/heldout2028" in a["href"] for a in conf_links)
    conf_abs = conf.select_one("details.pub-abstract").get_text(" ")
    assert "γ" in conf_abs and "$" not in conf_abs and "{" not in conf_abs

    pre = soup.select_one('#preprints section.pub-year[data-year="2028"] article[data-key="HeldoutPreprint2028"]')
    assert pre is not None and pre.get("data-pub-type") == "preprint"
    badge = pre.select_one("span.pub-badge-preprint")
    assert badge is not None and badge not in pre.select_one(".pub-title").descendants
    assert "økonomi and årsaker" in collapse_ws(pre.select_one(".pub-title").get_text(" "))
    assert pre.select_one("strong.pub-author-self").get_text(strip=True) == "Guido P. Biele"
    assert any("Preprint" in a.get_text() and "https://osf.io/preprints/heldout2028" in a["href"]
               for a in pre.select(".pub-links a[href]"))


def test_heldout_latex_unicode_rendering_across_fields(site):
    assert REFERENCES_BIB.is_file(), "data/references.bib missing"
    soup = soup_of(site / "publications.html")
    entries = soup.select("article.pub-entry")
    assert len(entries) == 69

    bad_tex = re.compile(r"\\(?:[A-Za-z]+|[&%$#_{}~^'\"`])")
    bad_entity = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")
    for art in entries:
        for sel in (".pub-title", ".pub-authors", ".pub-venue", "details.pub-abstract"):
            el = art.select_one(sel)
            if el is None:
                continue
            txt = el.get_text(" ")
            assert "{" not in txt and "}" not in txt, f"{art.get('data-key')} {sel}: {txt!r}"
            assert not bad_tex.search(txt), f"{art.get('data-key')} {sel}: {txt!r}"
            assert not bad_entity.search(txt), f"{art.get('data-key')} {sel}: {txt!r}"

    by_key = {a.get("data-key"): a for a in entries}
    assert "Tomás Varnet Pérez" in collapse_ws(by_key["VarnetPerez2025"].select_one(".pub-authors").get_text(" "))

    bib_entries = _parse_bib(REFERENCES_BIB.read_text(encoding="utf8"))
    eikemo_2017 = next(
        e for e in bib_entries
        if e["fields"].get("doi", "").strip().casefold() == "10.1038/npp.2017.58"
        or "opioidmodulationofvaluebaseddecision" in _pdf_norm(e["fields"].get("title", ""))
    )
    eikemo_abs = by_key[eikemo_2017["key"]].select_one("details.pub-abstract").get_text(" ")
    assert ("μ" in eikemo_abs or "µ" in eikemo_abs) and "$" not in eikemo_abs and "\\mu" not in eikemo_abs

    orstavik = next(
        e for e in bib_entries
        if "adhdinorge" in _pdf_norm(e["fields"].get("title", ""))
    )
    assert "Ørstavik" in collapse_ws(by_key[orstavik["key"]].select_one(".pub-authors").get_text(" "))

    brunborg = next(
        e for e in bib_entries
        if e["fields"].get("doi", "").strip().casefold() == "10.1016/j.socscimed.2026.119571"
    )
    assert "Social Science & Medicine" in collapse_ws(
        by_key[brunborg["key"]].select_one(".pub-venue").get_text(" ")
    )


def test_heldout_cv_pdf_metadata_and_annotations_privacy(site):
    assert CV_PDF.is_file(), "files/CV_GuidoBiele.pdf missing"
    reader = PdfReader(str(CV_PDF))
    assert len(reader.pages) >= 1

    meta_strings = []
    raw_info = reader.trailer.get("/Info")
    if raw_info is not None:
        for k, v in raw_info.get_object().items():
            meta_strings.append(f"{k}={v}")
    if reader.metadata:
        for k, v in reader.metadata.items():
            meta_strings.append(f"{k}={v}")
    root = reader.trailer["/Root"].get_object()
    if "/Metadata" in root:
        xmp_data = root["/Metadata"].get_object().get_data()
        meta_strings.append(xmp_data.decode("utf8", errors="replace"))

    uris = []
    for page in reader.pages:
        for annot in (page.get("/Annots") or []):
            obj = annot.get_object()
            uris.extend(_iter_action_uris(obj.get("/A")))

    non_body = "\n".join(meta_strings + uris)
    non_body_digits = "".join(c for c in non_body if c.isdigit())
    assert "21078350" not in non_body_digits
    assert "+47" not in non_body
    assert "guido.biele@fhi.no" not in non_body.casefold()
    assert "mailto:" not in non_body.casefold()
    assert not any(u.lower().startswith("tel:") for u in uris)
    assert any(
        u.lower().startswith(("http://", "https://"))
        and any(tok in u.lower() for tok in ("scholar.google", "tinyurl.com/gubipubs", "tinyurl.com/gbpubsgs"))
        for u in uris
    ), f"missing Google Scholar annotation URI in {uris!r}"

    page1 = collapse_ws(reader.pages[0].extract_text() or "")
    for retained in ("Norwegian Institute of Public Health", "Marcus Thranes gate 6", "0473 Oslo", "Google Scholar"):
        assert retained in page1, f"missing {retained!r} on page 1 of CV PDF"


def test_heldout_cv_pdf_bib_completeness_and_exclusions():
    assert REFERENCES_BIB.is_file(), "data/references.bib missing"
    assert CV_PDF.is_file(), "files/CV_GuidoBiele.pdf missing"
    bib_entries = _parse_bib(REFERENCES_BIB.read_text(encoding="utf8"))
    assert len(bib_entries) == 69

    reader = PdfReader(str(CV_PDF))
    cv_text = "\n".join(p.extract_text() or "" for p in reader.pages)
    cv_norm = _pdf_norm(cv_text)

    matched = 0
    for e in bib_entries:
        title_n = _pdf_norm(e["fields"]["title"])
        year = e["fields"]["year"].strip()
        first_author = _latex_to_unicode(e["fields"]["author"].split(" and ")[0].strip())
        fam = first_author.split(",", 1)[0].strip() if "," in first_author else first_author
        fam_tok = _pdf_norm(fam.split()[-1])
        assert title_n in cv_norm, f"CV PDF missing title for {e['key']}"
        assert fam_tok in cv_norm, f"CV PDF missing author {fam_tok!r} for {e['key']}"
        assert year in cv_text, f"CV PDF missing year {year} for {e['key']}"
        matched += 1
    assert matched == 69

    for excluded_title in (
        "Population-adjusted incidence of gender incongruence",
        "CPT performance predicts medication response",
        "Methylphenidate on Brain Processes",
        "discriminates medication persisters",
    ):
        assert _pdf_norm(excluded_title) not in cv_norm
