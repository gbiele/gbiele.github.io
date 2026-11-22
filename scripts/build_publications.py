"""Generate Quarto listing YAML files from data/references.bib."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from pathlib import Path

ALLOWED_TYPES = {"article", "inproceedings", "techreport", "misc", "unpublished"}
PUBLISHED_TYPES = {"article", "inproceedings", "techreport"}
PREPRINT_TYPES = {"misc", "unpublished"}

TYPE_LABELS = {
    "article": "Journal Article",
    "inproceedings": "Proceedings",
    "techreport": "Report",
    "preprint": "Preprint",
}

MONTH_MAP = {
    "jan": 1, "january": 1, "1": 1, "01": 1,
    "feb": 2, "february": 2, "2": 2, "02": 2,
    "mar": 3, "march": 3, "3": 3, "03": 3,
    "apr": 4, "april": 4, "4": 4, "04": 4,
    "may": 5, "5": 5, "05": 5,
    "jun": 6, "june": 6, "6": 6, "06": 6,
    "jul": 7, "july": 7, "7": 7, "07": 7,
    "aug": 8, "august": 8, "8": 8, "08": 8,
    "sep": 9, "sept": 9, "september": 9, "9": 9, "09": 9,
    "oct": 10, "october": 10, "10": 10,
    "nov": 11, "november": 11, "11": 11,
    "dec": 12, "december": 12, "12": 12,
}

_LIGATURES = {
    "aa": "å", "AA": "Å",
    "o": "ø", "O": "Ø",
    "ae": "æ", "AE": "Æ",
    "ss": "ß", "SS": "ẞ",
    "l": "ł", "L": "Ł",
    "oe": "œ", "OE": "Œ",
    "i": "i", "j": "j",
}

_ACCENTS = {
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    '"': "\u0308",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "c": "\u0327",
    "r": "\u030a",
    "k": "\u0328",
    "d": "\u0323",
    "b": "\u0331",
}

_MATH_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ",
    "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ",
    "Theta": "Θ", "Lambda": "Λ", "Sigma": "Σ", "Phi": "Φ",
    "Omega": "Ω",
    "geq": "≥", "ge": "≥", "leq": "≤", "le": "≤",
    "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡",
    "times": "×", "cdot": "·", "pm": "±", "mp": "∓",
    "infty": "∞", "rightarrow": "→", "to": "→", "leftarrow": "←",
    "leftrightarrow": "↔", "sim": "~",
}

_MATH_RE = re.compile(
    r"\\(" + "|".join(sorted(_MATH_SYMBOLS, key=len, reverse=True)) + r")(?![A-Za-z])"
)
_LIG_RE = re.compile(
    r"\{\\(" + "|".join(_LIGATURES) + r")\}|\\(" + "|".join(_LIGATURES) + r")(?![A-Za-z])"
)
_ACCENT_RE = re.compile(
    r"\{\\(['`^\"~=.])\{?([A-Za-z]|\\i(?![A-Za-z])|\\j(?![A-Za-z]))\}?\}|"
    r"\{\\([uvHcrkdb])(?:\s+|\s*\{)([A-Za-z]|\\i(?![A-Za-z])|\\j(?![A-Za-z]))\}?\}|"
    r"\\(['`^\"~=.])\{([A-Za-z]|\\i(?![A-Za-z])|\\j(?![A-Za-z]))\}|"
    r"\\([uvHcrkdb])\{([A-Za-z]|\\i(?![A-Za-z])|\\j(?![A-Za-z]))\}|"
    r"\\(['`^\"~=.])([A-Za-z])|"
    r"\\([uvHcrkdb])\s+([A-Za-z])"
)
_TEXT_WRAPPER_RE = re.compile(
    r"\\(?:emph|textit|textbf|textrm|text|mathrm|mathbf|mathit)\{([^{}]*)\}"
)


def _read_balanced(text: str, start: int, open_ch: str, close_ch: str, context: str) -> tuple[str, int]:
    depth = 1
    pos = start
    n = len(text)
    while pos < n and depth > 0:
        ch = text[pos]
        if ch == "\\" and pos + 1 < n:
            pos += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
        pos += 1
    if depth != 0:
        raise ValueError(f"Unclosed {open_ch}...{close_ch} in {context}")
    return text[start:pos - 1], pos


def parse_bib(text: str) -> list[dict]:
    """Parse and strictly validate BibTeX entries."""
    entries: list[dict] = []
    seen_keys: set[str] = set()
    entry_re = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
    pos = 0
    n = len(text)

    while pos < n:
        match = entry_re.search(text, pos)
        if not match:
            trailing = text[pos:]
            # Allow whitespace and % comments outside entries, reject stray @ tokens
            non_comment = "\n".join(
                line for line in trailing.splitlines() if not line.lstrip().startswith("%")
            )
            if "@" in non_comment:
                raise ValueError("Malformed '@' entry header near end of BibTeX file")
            break

        preamble = text[pos:match.start()]
        non_comment = "\n".join(
            line for line in preamble.splitlines() if not line.lstrip().startswith("%")
        )
        if "@" in non_comment:
            raise ValueError(f"Malformed '@' entry header before @{match.group(1)}")

        raw_type = match.group(1)
        kind = raw_type.lower()
        open_ch = match.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        body, end_pos = _read_balanced(text, match.end(), open_ch, close_ch, f"@{raw_type}")
        pos = end_pos

        if kind in ("comment", "string", "preamble"):
            continue
        if kind not in ALLOWED_TYPES:
            raise ValueError(f"Unsupported BibTeX entry type @{raw_type}")

        comma_idx = body.find(",")
        if comma_idx < 0:
            raise ValueError(f"Missing comma after citation key in @{raw_type}{open_ch}{body[:40]}...")
        key = body[:comma_idx].strip()
        if not key or any(ch.isspace() for ch in key):
            raise ValueError(f"Invalid or missing BibTeX citation key: {key!r}")
        if key in seen_keys:
            raise ValueError(f"Duplicate BibTeX citation key: {key!r}")
        seen_keys.add(key)

        rest = body[comma_idx + 1:]
        fields: dict[str, str] = {}
        fpos = 0
        rlen = len(rest)
        field_re = re.compile(r"\s*([A-Za-z_\-]+)\s*=\s*")

        while fpos < rlen:
            while fpos < rlen and (rest[fpos].isspace() or rest[fpos] == ","):
                fpos += 1
            if fpos >= rlen:
                break
            fm = field_re.match(rest, fpos)
            if not fm:
                snippet = rest[fpos:fpos + 40].strip()
                raise ValueError(f"Malformed field syntax in {key}: {snippet!r}")
            name = fm.group(1).lower()
            p = fm.end()
            if p >= rlen:
                raise ValueError(f"Missing field value for {key}.{name}")

            if rest[p] == "{":
                val, p = _read_balanced(rest, p + 1, "{", "}", f"{key}.{name}")
            elif rest[p] == '"':
                q = p + 1
                depth = 0
                while q < rlen and not (rest[q] == '"' and depth == 0):
                    if rest[q] == "\\" and q + 1 < rlen:
                        q += 2
                        continue
                    if rest[q] == "{":
                        depth += 1
                    elif rest[q] == "}":
                        depth -= 1
                    q += 1
                if q >= rlen:
                    raise ValueError(f"Unclosed quoted string in {key}.{name}")
                val, p = rest[p + 1:q], q + 1
            else:
                vm = re.match(r"[^,]*", rest[p:])
                val = vm.group(0).strip()
                p += vm.end()
                if not val:
                    raise ValueError(f"Empty bare field value in {key}.{name}")

            fields[name] = val.strip()
            while p < rlen and rest[p].isspace():
                p += 1
            if p < rlen:
                if rest[p] != ",":
                    raise ValueError(f"Expected comma after {key}.{name}, found {rest[p:p + 20]!r}")
                p += 1
            fpos = p

        title = tex_to_text(fields.get("title", ""))
        author = tex_to_text(fields.get("author", ""))
        year = fields.get("year", "").strip()
        if not title:
            raise ValueError(f"Missing required 'title' in {key}")
        if not author:
            raise ValueError(f"Missing required 'author' in {key}")
        if not re.fullmatch(r"\d{4}", year) or not (2000 <= int(year) <= 2099):
            raise ValueError(f"Invalid 'year' {year!r} in {key}; expected 4-digit year in [2000, 2099]")

        entries.append({"key": key, "type": kind, "fields": fields})

    return entries


def _replace_accent(match: re.Match[str]) -> str:
    cmd = (
        match.group(1) or match.group(3) or match.group(5)
        or match.group(7) or match.group(9) or match.group(11)
    )
    base = (
        match.group(2) or match.group(4) or match.group(6)
        or match.group(8) or match.group(10) or match.group(12)
    )
    if base.startswith("\\i"):
        base = "i"
    elif base.startswith("\\j"):
        base = "j"
    return unicodedata.normalize("NFC", base + _ACCENTS[cmd])


def tex_to_text(raw: str) -> str:
    """Convert LaTeX/BibTeX markup to clean Unicode text."""
    if not raw:
        return ""
    text = raw.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = text.replace("\\textbackslashtextquoteright", "'")

    for cmd, repl in (
        ("\\textendash", "–"),
        ("\\textemdash", "—"),
        ("\\textquoteright", "’"),
        ("\\textquoteleft", "‘"),
        ("\\textquotedblright", "”"),
        ("\\textquotedblleft", "“"),
    ):
        text = text.replace(cmd, repl)

    # Protect escaped literal $ before stripping inline math delimiters
    dollar_sentinel = "\x00DOLLAR\x00"
    text = text.replace(r"\$", dollar_sentinel)

    for _ in range(3):
        text = _TEXT_WRAPPER_RE.sub(r"\1", text)
        text = _ACCENT_RE.sub(_replace_accent, text)

    text = _MATH_RE.sub(lambda m: _MATH_SYMBOLS[m.group(1)], text)
    text = _LIG_RE.sub(lambda m: _LIGATURES[m.group(1) or m.group(2)], text)

    def _strip_math(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.strip() == "-":
            return "−"
        # Skip likely currency pairs like "$5 and $10"
        if re.match(r"^\d[\d,.\s]+\b", inner) and not re.search(r"[\\^_=<>+\-*/]", inner):
            return match.group(0)
        return inner

    text = re.sub(r"\$([^$\n]{1,120}?)\$", _strip_math, text)

    text = re.sub(r"\\([&%#_])", r"\1", text)
    text = text.replace("\\ ", " ")
    text = text.replace("~", " ").replace("\u00a0", " ")
    text = text.replace("---", "—").replace("--", "–")
    text = text.replace("``", "“").replace("''", "”")
    text = text.replace(dollar_sentinel, "$")
    text = text.replace("{", "").replace("}", "")
    return " ".join(text.split())


def _split_top_level_and(text: str) -> list[str]:
    """Split an author string on 'and' outside braces."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and ch.isspace():
            m = re.match(r"\s+and\s+", text[i:], re.I)
            if m:
                parts.append(text[start:i].strip())
                i += m.end()
                start = i
                continue
        i += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    parts.append(text[start:].strip())
    return parts


def _split_top_level_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    depth = 0
    start = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            if start is None:
                start = i
            i += 2
            continue
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch.isspace() and depth == 0:
            if start is not None:
                tokens.append(text[start:i])
                start = None
        else:
            if start is None:
                start = i
        i += 1
    if start is not None:
        tokens.append(text[start:])
    return tokens


def _is_guido_biele(first: str, last: str) -> bool:
    if last.strip().casefold() != "biele":
        return False
    given = [t.casefold() for t in first.split() if t]
    if not given:
        return False
    first_ok = given[0] in {"guido", "g", "g.", "gudio"} or bool(
        re.fullmatch(r"g\.(?:[a-z]\.?)+", given[0])
    )
    if not first_ok:
        return False
    return all(re.fullmatch(r"(?:[a-z]\.?)+|philipp|emmanuel", tok) for tok in given[1:])


def format_authors_html(author_field: str) -> str:
    """Format BibTeX author list and highlight Guido Biele."""
    rendered: list[str] = []
    for raw in _split_top_level_and(author_field):
        if raw.strip().strip("{}").casefold() == "others":
            rendered.append("et al.")
            continue

        comma_parts = _split_top_level_commas(raw)
        if len(comma_parts) > 1:
            last = tex_to_text(comma_parts[0])
            first = tex_to_text(comma_parts[-1])
            jr = ", ".join(tex_to_text(p) for p in comma_parts[1:-1] if tex_to_text(p))
            display = f"{first} {last}".strip()
            if jr:
                display = f"{display}, {jr}"
        else:
            cleaned = tex_to_text(raw)
            plain_tokens = cleaned.split()
            if len(plain_tokens) >= 2 and re.fullmatch(r"[A-Z]{1,4}", plain_tokens[-1]):
                last = " ".join(plain_tokens[:-1])
                first = " ".join(f"{ch}." for ch in plain_tokens[-1])
                display = f"{first} {last}".strip()
            else:
                top_tokens = _split_top_level_tokens(raw)
                if len(top_tokens) >= 2:
                    last = tex_to_text(top_tokens[-1])
                    first = tex_to_text(" ".join(top_tokens[:-1]))
                else:
                    last = cleaned
                    first = ""
                display = f"{first} {last}".strip()

        escaped = html.escape(display, quote=False)
        if _is_guido_biele(first, last):
            rendered.append(f'<strong class="pub-author-self">{escaped}</strong>')
        else:
            rendered.append(escaped)

    return ", ".join(rendered)


def format_venue(entry_type: str, fields: dict[str, str]) -> str:
    """Build a non-empty venue string for an entry."""
    journal = tex_to_text(fields.get("journal", ""))
    booktitle = tex_to_text(fields.get("booktitle", ""))
    series = tex_to_text(fields.get("series", ""))
    howpublished = tex_to_text(fields.get("howpublished", ""))
    institution = tex_to_text(fields.get("institution", ""))
    publisher = tex_to_text(fields.get("publisher", ""))
    note = tex_to_text(fields.get("note", ""))
    volume = tex_to_text(fields.get("volume", ""))
    number = tex_to_text(fields.get("number", ""))
    pages = tex_to_text(fields.get("pages", ""))

    if entry_type == "article":
        base = journal or booktitle or publisher or "Article"
        parts = [base]
        vol_num = ""
        if volume and number:
            vol_num = f"{volume}({number})"
        elif volume:
            vol_num = volume
        if vol_num:
            parts.append(vol_num)
        if pages:
            parts.append(pages)
        return ", ".join(parts)

    if entry_type == "inproceedings":
        base = booktitle or journal or tex_to_text(fields.get("conference", "")) or "Conference Proceedings"
        parts = [base]
        if series and volume:
            parts.append(f"{series}, vol. {volume}")
        elif volume:
            parts.append(f"vol. {volume}")
        if pages:
            parts.append(f"pp. {pages}")
        return ", ".join(parts)

    if entry_type == "techreport":
        base = institution or publisher or "Technical Report"
        if number:
            return f"{base}, Report {number}"
        return base

    return howpublished or journal or institution or publisher or note or "Preprint"


def _normalize_doi(raw_doi: str) -> str:
    doi = raw_doi.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.strip()


def _canonical_href(href: str) -> str:
    clean = href.strip()
    m = re.match(r"^https?://(?:dx\.)?doi\.org/(.+)$", clean, flags=re.I)
    if m:
        return f"https://doi.org/{m.group(1).strip()}".casefold()
    return clean.casefold()


def build_links(entry_type: str, fields: dict[str, str]) -> list[dict[str, str]]:
    """Construct deduplicated external/local links in priority order."""
    links: list[dict[str, str]] = []
    seen_hrefs: set[str] = set()

    def add_link(label: str, href: str) -> None:
        href = href.strip()
        if not href:
            return
        canon = _canonical_href(href)
        if canon in seen_hrefs:
            return
        seen_hrefs.add(canon)
        links.append({"label": label, "href": href})

    raw_doi = fields.get("doi", "").strip()
    if raw_doi:
        clean_doi = _normalize_doi(raw_doi)
        if clean_doi:
            add_link("DOI", f"https://doi.org/{clean_doi}")

    pdf_val = (fields.get("pdf") or fields.get("url_pdf") or "").strip()
    if pdf_val:
        add_link("PDF", pdf_val)

    url_preprint = fields.get("url_preprint", "").strip()
    eprint = fields.get("eprint", "").strip()
    archive_prefix = (fields.get("archiveprefix") or fields.get("eprinttype") or "").strip().casefold()

    if url_preprint:
        add_link("Preprint", url_preprint)
    elif eprint:
        arxiv_match = re.match(r"^arxiv:(.+)$", eprint, flags=re.I)
        if arxiv_match:
            add_link("Preprint", f"https://arxiv.org/abs/{arxiv_match.group(1).strip()}")
        elif archive_prefix == "arxiv":
            add_link("Preprint", f"https://arxiv.org/abs/{eprint}")
        elif eprint.lower().startswith(("http://", "https://")):
            add_link("Preprint", eprint)

    code_val = (fields.get("code") or fields.get("url_code") or "").strip()
    if code_val:
        add_link("Code", code_val)

    url_val = fields.get("url", "").strip()
    if url_val:
        url_label = "Proceedings" if entry_type == "inproceedings" else (
            "Report" if entry_type == "techreport" else "URL"
        )
        add_link(url_label, url_val)

    return links


def build_listing_items(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Transform parsed BibTeX entries into sorted published and preprint items."""
    published: list[dict] = []
    preprints: list[dict] = []

    def sort_key(entry: dict) -> tuple[int, int, str]:
        year_int = int(entry["fields"]["year"])
        month_raw = entry["fields"].get("month", "").strip().lower()
        month_int = MONTH_MAP.get(month_raw, 0)
        return (-year_int, -month_int, entry["key"].casefold())

    for entry in sorted(entries, key=sort_key):
        etype = entry["type"]
        fields = entry["fields"]
        pub_type = "preprint" if etype in PREPRINT_TYPES else etype

        title_text = tex_to_text(fields["title"])
        venue_text = format_venue(etype, fields)
        abstract_text = tex_to_text(fields.get("abstract", ""))

        item = {
            "key": entry["key"],
            "year": fields["year"].strip(),
            "pub_type": pub_type,
            "type_label": TYPE_LABELS[pub_type],
            "title_html": html.escape(title_text, quote=False),
            "authors_html": format_authors_html(fields["author"]),
            "venue_html": html.escape(venue_text, quote=False),
            "abstract_html": html.escape(abstract_text, quote=False) if abstract_text else "",
            "links": build_links(etype, fields),
        }

        if pub_type == "preprint":
            preprints.append(item)
        else:
            published.append(item)

    return published, preprints


def dump_yaml_items(items: list[dict]) -> str:
    """Serialize listing items as YAML using Python stdlib json string quoting."""
    lines: list[str] = []
    for item in items:
        lines.append(f"- key: {json.dumps(item['key'], ensure_ascii=False)}")
        for field in (
            "year",
            "pub_type",
            "type_label",
            "title_html",
            "authors_html",
            "venue_html",
            "abstract_html",
        ):
            lines.append(f"  {field}: {json.dumps(item[field], ensure_ascii=False)}")
        if item["links"]:
            lines.append("  links:")
            for link in item["links"]:
                lines.append(f"    - label: {json.dumps(link['label'], ensure_ascii=False)}")
                lines.append(f"      href: {json.dumps(link['href'], ensure_ascii=False)}")
        else:
            lines.append("  links: []")
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    bib_path = root / "data" / "references.bib"
    if not bib_path.is_file():
        raise FileNotFoundError(f"Missing bibliography file: {bib_path}")

    raw_bib = bib_path.read_text(encoding="utf8")
    entries = parse_bib(raw_bib)
    published, preprints = build_listing_items(entries)

    out_dir = root / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "publications.yml").write_text(dump_yaml_items(published), encoding="utf8", newline="\n")
    (out_dir / "preprints.yml").write_text(dump_yaml_items(preprints), encoding="utf8", newline="\n")


if __name__ == "__main__":
    main()
