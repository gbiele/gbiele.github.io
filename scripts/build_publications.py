"""Generate Quarto listing YAML files from data/references.bib."""

from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
from pathlib import Path

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("causalinf", "Causal inference"),
    ("bayes", "Bayesian statistics"),
    ("adhd", "ADHD"),
    ("epi", "Epidemiology"),
    ("decision-making", "Decision making & neuroscience"),
)
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)
CATEGORY_ORDER: dict[str, int] = {slug: idx for idx, (slug, _) in enumerate(CATEGORIES)}

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


def _is_external_or_root_href(href: str) -> bool:
    return bool(re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*:|//|/|#)", href))


def build_links(entry_type: str, fields: dict[str, str], relative_prefix: str = "") -> list[dict[str, str]]:
    """Construct deduplicated external/local links in priority order."""
    links: list[dict[str, str]] = []
    seen_hrefs: set[str] = set()

    def add_link(label: str, href: str) -> None:
        href = href.strip()
        if not href:
            return
        if relative_prefix and not _is_external_or_root_href(href):
            href = f"{relative_prefix}{href}"
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


def _entry_sort_key(entry: dict) -> tuple[int, int, str]:
    year_int = int(entry["fields"]["year"])
    month_raw = entry["fields"].get("month", "").strip().lower()
    month_int = MONTH_MAP.get(month_raw, 0)
    return (-year_int, -month_int, entry["key"].casefold())


def _author_family_name(raw_author: str) -> str:
    comma_parts = _split_top_level_commas(raw_author)
    if len(comma_parts) > 1:
        return tex_to_text(comma_parts[0])
    cleaned = tex_to_text(raw_author)
    plain_tokens = cleaned.split()
    if len(plain_tokens) >= 2 and re.fullmatch(r"[A-Z]{1,4}", plain_tokens[-1]):
        return " ".join(plain_tokens[:-1])
    top_tokens = _split_top_level_tokens(raw_author)
    if len(top_tokens) >= 2:
        return tex_to_text(top_tokens[-1])
    return cleaned


def _format_short_citation(author_field: str, year: str) -> str:
    authors = [a for a in _split_top_level_and(author_field) if a.strip().strip("{}").casefold() != "others"]
    if not authors:
        return f"({year.strip()})"
    fam1 = _author_family_name(authors[0])
    if len(authors) == 1:
        return f"{fam1} ({year.strip()})"
    if len(authors) == 2:
        fam2 = _author_family_name(authors[1])
        return f"{fam1} & {fam2} ({year.strip()})"
    return f"{fam1} et al. ({year.strip()})"


# --- Minimal stdlib YAML parser for data/*.yml -----------------------------


def _strip_yaml_comment(text: str) -> str:
    in_single = False
    in_double = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        elif in_double:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "#" and (i == 0 or text[i - 1].isspace()):
                return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _split_yaml_kv(text: str) -> tuple[str, str] | None:
    in_single = False
    in_double = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        elif in_double:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == ":" and (i + 1 == n or text[i + 1].isspace()):
                return text[:i].strip(), text[i + 1:].strip()
        i += 1
    return None


def _unescape_double_quoted(inner: str) -> str:
    try:
        return json.loads(f'"{inner}"')
    except json.JSONDecodeError:
        out: list[str] = []
        i = 0
        n = len(inner)
        esc = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/"}
        while i < n:
            if inner[i] == "\\" and i + 1 < n:
                nxt = inner[i + 1]
                if nxt == "u" and i + 5 < n:
                    out.append(chr(int(inner[i + 2:i + 6], 16)))
                    i += 6
                    continue
                out.append(esc.get(nxt, nxt))
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)


def _parse_yaml_scalar(raw: str):
    s = _strip_yaml_comment(raw).strip()
    if not s:
        return ""
    if s[0] == '"' and len(s) >= 2 and s[-1] == '"':
        return _unescape_double_quoted(s[1:-1])
    if s[0] == "'" and len(s) >= 2 and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s in ("null", "Null", "NULL", "~"):
        return None
    if re.fullmatch(r"-?(?:0|[1-9]\d*)", s):
        return int(s)
    return s


def _find_closing_quote(text: str, quote_ch: str) -> int:
    """Return index of closing quote in text (which starts with quote_ch), or -1."""
    i = 1
    n = len(text)
    while i < n:
        ch = text[i]
        if quote_ch == "'":
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                return i
        else:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                return i
        i += 1
    return -1


def _consume_quoted_scalar(lines: list[str], idx: int, first_part: str) -> tuple[str, int]:
    quote_ch = first_part[0]
    buf = first_part
    cur = idx + 1
    while True:
        close_pos = _find_closing_quote(buf, quote_ch)
        if close_pos != -1:
            trailing = _strip_yaml_comment(buf[close_pos + 1:]).strip()
            if trailing:
                raise ValueError(f"Unexpected trailing YAML content after quoted string: {trailing!r}")
            inner = buf[1:close_pos]
            val = inner.replace("''", "'") if quote_ch == "'" else _unescape_double_quoted(inner)
            return val, cur
        if cur >= len(lines):
            raise ValueError(f"Unclosed {quote_ch}-quoted YAML string near line {idx + 1}")
        nxt = lines[cur].strip()
        buf = (buf + "\n") if not nxt else (buf + " " + nxt)
        cur += 1


def _consume_flow_list(lines: list[str], idx: int, first_part: str) -> tuple[list, int]:
    buf = _strip_yaml_comment(first_part)
    cur = idx + 1
    while True:
        # Check for top-level closing ']'
        in_s = False
        in_d = False
        close_pos = -1
        i = 0
        while i < len(buf):
            ch = buf[i]
            if in_s:
                if ch == "'":
                    if i + 1 < len(buf) and buf[i + 1] == "'":
                        i += 2
                        continue
                    in_s = False
            elif in_d:
                if ch == "\\" and i + 1 < len(buf):
                    i += 2
                    continue
                if ch == '"':
                    in_d = False
            else:
                if ch == "'":
                    in_s = True
                elif ch == '"':
                    in_d = True
                elif ch == "]":
                    close_pos = i
                    break
            i += 1
        if close_pos != -1:
            inner = buf[1:close_pos].strip()
            if not inner:
                return [], cur
            items = []
            start = 0
            in_s = False
            in_d = False
            j = 0
            while j < len(inner):
                ch = inner[j]
                if in_s:
                    if ch == "'":
                        if j + 1 < len(inner) and inner[j + 1] == "'":
                            j += 2
                            continue
                        in_s = False
                elif in_d:
                    if ch == "\\" and j + 1 < len(inner):
                        j += 2
                        continue
                    if ch == '"':
                        in_d = False
                else:
                    if ch == "'":
                        in_s = True
                    elif ch == '"':
                        in_d = True
                    elif ch == ",":
                        items.append(_parse_yaml_scalar(inner[start:j]))
                        start = j + 1
                j += 1
            tail = inner[start:].strip()
            if tail:
                items.append(_parse_yaml_scalar(tail))
            return items, cur
        if cur >= len(lines):
            raise ValueError(f"Unclosed YAML flow list near line {idx + 1}")
        buf += " " + _strip_yaml_comment(lines[cur]).strip()
        cur += 1


def _consume_block_scalar(lines: list[str], idx: int, indicator: str, parent_indent: int) -> tuple[str, int]:
    folded = indicator.startswith(">")
    strip_trail = indicator.endswith("-")
    cur = idx + 1
    content_indent: int | None = None
    collected: list[str] = []
    while cur < len(lines):
        raw_line = lines[cur]
        if not raw_line.strip():
            collected.append("")
            cur += 1
            continue
        ind = len(raw_line) - len(raw_line.lstrip(" "))
        if ind <= parent_indent:
            break
        if content_indent is None:
            content_indent = ind
        collected.append(raw_line[min(content_indent, ind):])
        cur += 1
    if folded:
        text = " ".join(part.strip() for part in collected if part.strip())
    else:
        text = "\n".join(collected)
    if strip_trail:
        text = text.rstrip("\n")
    return text, cur


def _skip_blank_and_comments(lines: list[str], idx: int) -> int:
    n = len(lines)
    while idx < n:
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        break
    return idx


def _parse_yaml_value(lines: list[str], idx: int, val_str: str, key_indent: int):
    val_clean = _strip_yaml_comment(val_str).strip()
    if not val_clean:
        nxt = _skip_blank_and_comments(lines, idx + 1)
        if nxt >= len(lines):
            return None, nxt
        nxt_line = lines[nxt]
        nxt_indent = len(nxt_line) - len(nxt_line.lstrip(" "))
        nxt_stripped = nxt_line.lstrip(" ")
        if nxt_stripped.startswith("- ") or nxt_stripped == "-":
            if nxt_indent >= key_indent:
                return _parse_yaml_sequence(lines, nxt, nxt_indent)
        if nxt_indent > key_indent:
            return _parse_yaml_block(lines, nxt, nxt_indent)
        return None, nxt

    if val_clean[0] in ("'", '"'):
        return _consume_quoted_scalar(lines, idx, val_str.strip())
    if val_clean.startswith("["):
        return _consume_flow_list(lines, idx, val_str.strip())
    if val_clean in ("|", ">", "|-", ">-"):
        return _consume_block_scalar(lines, idx, val_clean, key_indent)

    # Plain scalar, possibly wrapped across continuation lines with greater indent
    parts = [val_clean]
    cur = idx + 1
    while cur < len(lines):
        raw_line = lines[cur]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            break
        ind = len(raw_line) - len(raw_line.lstrip(" "))
        if ind <= key_indent:
            break
        parts.append(_strip_yaml_comment(stripped))
        cur += 1
    return _parse_yaml_scalar(" ".join(parts)), cur


def _parse_yaml_mapping(lines: list[str], idx: int, map_indent: int) -> tuple[dict, int]:
    result: dict = {}
    n = len(lines)
    while idx < n:
        idx = _skip_blank_and_comments(lines, idx)
        if idx >= n:
            break
        line = lines[idx]
        ind = len(line) - len(line.lstrip(" "))
        if ind < map_indent:
            break
        if ind > map_indent:
            raise ValueError(f"Unexpected YAML indentation at line {idx + 1}: {line!r}")
        stripped = line[ind:]
        if stripped.startswith("- ") or stripped == "-":
            break
        kv = _split_yaml_kv(stripped)
        if kv is None:
            raise ValueError(f"Expected YAML key: value at line {idx + 1}: {line!r}")
        raw_key, val_str = kv
        key = str(_parse_yaml_scalar(raw_key))
        val, idx = _parse_yaml_value(lines, idx, val_str, map_indent)
        result[key] = val
    return result, idx


def _parse_yaml_sequence(lines: list[str], idx: int, seq_indent: int) -> tuple[list, int]:
    result: list = []
    n = len(lines)
    while idx < n:
        idx = _skip_blank_and_comments(lines, idx)
        if idx >= n:
            break
        line = lines[idx]
        ind = len(line) - len(line.lstrip(" "))
        if ind < seq_indent:
            break
        if ind > seq_indent:
            raise ValueError(f"Unexpected YAML sequence indentation at line {idx + 1}: {line!r}")
        stripped = line[ind:]
        if not (stripped.startswith("- ") or stripped == "-"):
            break
        after_dash = stripped[2:] if stripped.startswith("- ") else ""
        item_indent = ind + 2
        if not _strip_yaml_comment(after_dash).strip():
            nxt = _skip_blank_and_comments(lines, idx + 1)
            if nxt >= n:
                result.append(None)
                idx = nxt
            else:
                nxt_ind = len(lines[nxt]) - len(lines[nxt].lstrip(" "))
                if nxt_ind > ind:
                    val, idx = _parse_yaml_block(lines, nxt, nxt_ind)
                    result.append(val)
                else:
                    result.append(None)
                    idx = nxt
            continue

        kv = _split_yaml_kv(after_dash)
        if kv is not None and not after_dash.lstrip().startswith(("'", '"', "[")):
            raw_key, val_str = kv
            key = str(_parse_yaml_scalar(raw_key))
            val, idx = _parse_yaml_value(lines, idx, val_str, item_indent)
            mapping = {key: val}
            nxt = _skip_blank_and_comments(lines, idx)
            if nxt < n:
                nxt_ind = len(lines[nxt]) - len(lines[nxt].lstrip(" "))
                if nxt_ind >= item_indent:
                    rest_map, idx = _parse_yaml_mapping(lines, nxt, nxt_ind)
                    mapping.update(rest_map)
            result.append(mapping)
        else:
            val, idx = _parse_yaml_value(lines, idx, after_dash, ind)
            result.append(val)
    return result, idx


def _parse_yaml_block(lines: list[str], idx: int, indent: int):
    idx = _skip_blank_and_comments(lines, idx)
    if idx >= len(lines):
        return None, idx
    stripped = lines[idx][indent:]
    if stripped.startswith("- ") or stripped == "-":
        return _parse_yaml_sequence(lines, idx, indent)
    return _parse_yaml_mapping(lines, idx, indent)


def parse_yaml_stdlib(text: str):
    """Parse standard YAML subset (mappings, sequences, scalars) using stdlib only."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    idx = _skip_blank_and_comments(lines, 0)
    if idx >= len(lines):
        return None
    first_indent = len(lines[idx]) - len(lines[idx].lstrip(" "))
    obj, end_idx = _parse_yaml_block(lines, idx, first_indent)
    end_idx = _skip_blank_and_comments(lines, end_idx)
    if end_idx < len(lines):
        raise ValueError(f"Unexpected trailing YAML content at line {end_idx + 1}: {lines[end_idx]!r}")
    return obj


def parse_publication_categories(
    text: str,
    valid_bib_keys: set[str] | None = None,
) -> dict[str, list[str]]:
    """Parse and strictly validate data/publication_categories.yml."""
    raw = parse_yaml_stdlib(text)
    if not isinstance(raw, dict):
        raise ValueError("data/publication_categories.yml must be a YAML mapping")

    valid_cats = set(CATEGORY_LABELS)
    result: dict[str, list[str]] = {}
    for key, cats in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Invalid BibTeX key in publication_categories.yml: {key!r}")
        if valid_bib_keys is not None and key not in valid_bib_keys:
            raise ValueError(f"Orphan BibTeX key in data/publication_categories.yml: {key!r}")
        if not isinstance(cats, list) or not cats:
            raise ValueError(f"{key}: categories must be a non-empty list, got {cats!r}")
        seen: set[str] = set()
        for c in cats:
            if not isinstance(c, str) or c not in valid_cats:
                raise ValueError(f"{key}: invalid category ID {c!r}; valid categories: {sorted(valid_cats)}")
            if c in seen:
                raise ValueError(f"{key}: duplicate category ID {c!r}")
            seen.add(c)
        result[key] = sorted(cats, key=lambda c: CATEGORY_ORDER[c])
    return result


def parse_projects(
    text: str,
    valid_bib_keys: set[str] | None = None,
) -> list[dict]:
    """Parse and strictly validate data/projects.yml."""
    raw = parse_yaml_stdlib(text)
    if not isinstance(raw, list):
        raise ValueError("data/projects.yml must be a YAML list")

    valid_cats = set(CATEGORY_LABELS)
    seen_ids: set[str] = set()
    projects: list[dict] = []

    for idx, proj in enumerate(raw):
        if not isinstance(proj, dict):
            raise ValueError(f"Project entry #{idx + 1} must be a mapping")
        pid = proj.get("id")
        if not isinstance(pid, str) or not pid.strip() or any(ch.isspace() for ch in pid):
            raise ValueError(f"Invalid project id at entry #{idx + 1}: {pid!r}")
        if pid in seen_ids:
            raise ValueError(f"Duplicate project id: {pid!r}")
        seen_ids.add(pid)

        for field in ("title", "role", "period", "description"):
            val = proj.get(field)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"Project {pid!r}: missing or empty {field!r}")

        funder = proj.get("funder", "")
        if funder is None:
            funder = ""
        if not isinstance(funder, str):
            raise ValueError(f"Project {pid!r}: 'funder' must be a string, got {funder!r}")

        year = proj.get("year")
        if not isinstance(year, int) or isinstance(year, bool) or not (1990 <= year <= 2099):
            raise ValueError(f"Project {pid!r}: invalid 4-digit integer year {year!r}")
        if str(year) not in proj["period"]:
            raise ValueError(f"Project {pid!r}: period {proj['period']!r} must contain year {year}")

        status = proj.get("status")
        if status not in ("current", "past"):
            raise ValueError(f"Project {pid!r}: status must be 'current' or 'past', got {status!r}")

        cats = proj.get("categories")
        if not isinstance(cats, list) or not cats:
            raise ValueError(f"Project {pid!r}: 'categories' must be a non-empty list")
        seen_cats: set[str] = set()
        for c in cats:
            if not isinstance(c, str) or c not in valid_cats:
                raise ValueError(f"Project {pid!r}: invalid category ID {c!r}")
            if c in seen_cats:
                raise ValueError(f"Project {pid!r}: duplicate category ID {c!r}")
            seen_cats.add(c)

        url = proj.get("url", "")
        if url is None:
            url = ""
        if not isinstance(url, str):
            raise ValueError(f"Project {pid!r}: 'url' must be a string, got {url!r}")

        papers = proj.get("papers")
        if not isinstance(papers, list):
            raise ValueError(f"Project {pid!r}: 'papers' must be a list")
        for pkey in papers:
            if not isinstance(pkey, str) or not pkey.strip():
                raise ValueError(f"Project {pid!r}: invalid paper key {pkey!r}")
            if valid_bib_keys is not None and pkey not in valid_bib_keys:
                raise ValueError(f"Project {pid!r}: unknown paper BibTeX key {pkey!r}")

        projects.append({
            "id": pid,
            "title": proj["title"].strip(),
            "role": proj["role"].strip(),
            "funder": funder.strip(),
            "year": year,
            "period": proj["period"].strip(),
            "status": status,
            "categories": list(cats),
            "url": url.strip(),
            "description": proj["description"].strip(),
            "papers": list(papers),
        })

    return projects


def parse_research_interests(
    text: str,
    valid_bib_keys: set[str] | None = None,
) -> list[dict]:
    """Parse and strictly validate data/research_interests.yml."""
    raw = parse_yaml_stdlib(text)
    if not isinstance(raw, list) or not raw:
        raise ValueError("data/research_interests.yml must be a non-empty YAML list")

    valid_link_targets = {f"{slug}.html" for slug, _ in CATEGORIES}
    seen_ids: set[str] = set()
    interests: list[dict] = []

    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Research interest entry #{idx + 1} must be a mapping")

        iid = entry.get("id")
        if not isinstance(iid, str) or not iid.strip() or any(ch.isspace() for ch in iid):
            raise ValueError(f"Invalid research interest id at entry #{idx + 1}: {iid!r}")
        if iid in seen_ids:
            raise ValueError(f"Duplicate research interest id: {iid!r}")
        seen_ids.add(iid)

        for field in ("title", "description"):
            val = entry.get(field)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"Research interest {iid!r}: missing or empty {field!r}")

        links = entry.get("links")
        if not isinstance(links, list) or not links:
            raise ValueError(f"Research interest {iid!r}: 'links' must be a non-empty list")
        parsed_links: list[dict[str, str]] = []
        seen_hrefs: set[str] = set()
        for lk in links:
            if not isinstance(lk, dict):
                raise ValueError(f"Research interest {iid!r}: link entry must be a mapping, got {lk!r}")
            label = lk.get("label")
            href = lk.get("href")
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"Research interest {iid!r}: link missing or empty 'label': {lk!r}")
            if not isinstance(href, str) or href.strip() not in valid_link_targets:
                raise ValueError(
                    f"Research interest {iid!r}: invalid link target {href!r}; "
                    f"expected one of {sorted(valid_link_targets)}"
                )
            clean_href = href.strip()
            if clean_href in seen_hrefs:
                raise ValueError(f"Research interest {iid!r}: duplicate link target {clean_href!r}")
            seen_hrefs.add(clean_href)
            parsed_links.append({"label": label.strip(), "href": clean_href})

        papers = entry.get("papers")
        if not isinstance(papers, list) or not (2 <= len(papers) <= 3):
            raise ValueError(
                f"Research interest {iid!r}: 'papers' must be a list of 2 to 3 BibTeX keys, got {papers!r}"
            )
        seen_papers: set[str] = set()
        clean_papers: list[str] = []
        for pkey in papers:
            if not isinstance(pkey, str) or not pkey.strip():
                raise ValueError(f"Research interest {iid!r}: invalid paper key {pkey!r}")
            key = pkey.strip()
            if key in seen_papers:
                raise ValueError(f"Research interest {iid!r}: duplicate paper key {key!r}")
            if valid_bib_keys is not None and key not in valid_bib_keys:
                raise ValueError(f"Research interest {iid!r}: unknown paper BibTeX key {key!r}")
            seen_papers.add(key)
            clean_papers.append(key)

        interests.append({
            "id": iid,
            "title": entry["title"].strip(),
            "description": entry["description"].strip(),
            "links": parsed_links,
            "papers": clean_papers,
        })

    return interests


def _build_single_listing_item(
    entry: dict,
    pub_categories: dict[str, list[str]] | None = None,
    relative_prefix: str = "",
    warn_uncategorized: bool = False,
) -> dict:
    etype = entry["type"]
    fields = entry["fields"]
    key = entry["key"]
    pub_type = "preprint" if etype in PREPRINT_TYPES else etype

    title_text = tex_to_text(fields["title"])
    venue_text = format_venue(etype, fields)
    abstract_text = tex_to_text(fields.get("abstract", ""))

    cats: list[str] = []
    if pub_categories is not None:
        if key in pub_categories:
            cats = sorted(pub_categories[key], key=lambda c: CATEGORY_ORDER[c])
        else:
            if warn_uncategorized:
                print(
                    f"Warning: BibTeX entry {key!r} is missing from data/publication_categories.yml",
                    file=sys.stderr,
                )

    cat_href_prefix = "" if relative_prefix == "../" else "research/"
    category_objs = [
        {"id": c, "label": CATEGORY_LABELS[c], "href": f"{cat_href_prefix}{c}.html"}
        for c in cats
    ]

    return {
        "key": key,
        "year": fields["year"].strip(),
        "pub_type": pub_type,
        "type_label": TYPE_LABELS[pub_type],
        "title_html": html.escape(title_text, quote=False),
        "authors_html": format_authors_html(fields["author"]),
        "venue_html": html.escape(venue_text, quote=False),
        "abstract_html": html.escape(abstract_text, quote=False) if abstract_text else "",
        "links": build_links(etype, fields, relative_prefix=relative_prefix),
        "category_ids": " ".join(cats),
        "categories": category_objs,
    }


def build_listing_items(
    entries: list[dict],
    pub_categories: dict[str, list[str]] | None = None,
    relative_prefix: str = "",
    warn_uncategorized: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Transform parsed BibTeX entries into sorted published and preprint items."""
    published: list[dict] = []
    preprints: list[dict] = []

    for entry in sorted(entries, key=_entry_sort_key):
        item = _build_single_listing_item(
            entry,
            pub_categories=pub_categories,
            relative_prefix=relative_prefix,
            warn_uncategorized=warn_uncategorized,
        )
        if item["pub_type"] == "preprint":
            preprints.append(item)
        else:
            published.append(item)

    return published, preprints


def build_category_publication_items(
    entries: list[dict],
    pub_categories: dict[str, list[str]],
    category_slug: str,
) -> list[dict]:
    """Build combined published + preprint items for a single research category page."""
    items: list[dict] = []
    for entry in sorted(entries, key=_entry_sort_key):
        if category_slug in pub_categories.get(entry["key"], ()):
            items.append(
                _build_single_listing_item(
                    entry,
                    pub_categories=pub_categories,
                    relative_prefix="../",
                    warn_uncategorized=False,
                )
            )
    return items


def build_category_project_items(
    projects: list[dict],
    entries_by_key: dict[str, dict],
    category_slug: str,
) -> list[dict]:
    """Build sorted project items for a single research category page."""
    indexed = [
        (idx, proj)
        for idx, proj in enumerate(projects)
        if category_slug in proj["categories"]
    ]
    indexed.sort(key=lambda pair: (-pair[1]["year"], pair[0]))

    items: list[dict] = []
    for _, proj in indexed:
        resolved_papers: list[dict[str, str]] = []
        for pkey in proj["papers"]:
            entry = entries_by_key[pkey]
            fields = entry["fields"]
            links = build_links(entry["type"], fields, relative_prefix="../")
            primary = next((lk for lk in links if lk["label"] != "Code"), None)
            if primary is None and links:
                primary = links[0]
            href = primary["href"] if primary is not None else "../publications.html"
            resolved_papers.append({
                "key": pkey,
                "citation": _format_short_citation(fields["author"], fields["year"]),
                "title": tex_to_text(fields["title"]),
                "href": href,
            })

        items.append({
            "id": proj["id"],
            "title": proj["title"],
            "role": proj["role"],
            "funder": proj["funder"],
            "year": proj["year"],
            "period": proj["period"],
            "status": proj["status"],
            "url": proj["url"],
            "description": proj["description"],
            "papers": resolved_papers,
        })
    return items


def build_research_interest_items(
    interests: list[dict],
    entries_by_key: dict[str, dict],
) -> list[dict]:
    """Build research interest listing items with resolved highlighted papers."""
    items: list[dict] = []
    for interest in interests:
        resolved_papers = [
            _build_single_listing_item(
                entries_by_key[pkey],
                pub_categories=None,
                relative_prefix="../",
                warn_uncategorized=False,
            )
            for pkey in interest["papers"]
        ]
        items.append({
            "id": interest["id"],
            "title": interest["title"],
            "description": interest["description"],
            "links": list(interest["links"]),
            "papers": resolved_papers,
        })
    return items


def dump_yaml_items(items: list[dict]) -> str:
    """Serialize publication listing items as YAML using Python stdlib json string quoting."""
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
            "category_ids",
        ):
            lines.append(f"  {field}: {json.dumps(item.get(field, ''), ensure_ascii=False)}")
        if item["links"]:
            lines.append("  links:")
            for link in item["links"]:
                lines.append(f"    - label: {json.dumps(link['label'], ensure_ascii=False)}")
                lines.append(f"      href: {json.dumps(link['href'], ensure_ascii=False)}")
        else:
            lines.append("  links: []")
        cats = item.get("categories") or []
        if cats:
            lines.append("  categories:")
            for cat in cats:
                lines.append(f"    - id: {json.dumps(cat['id'], ensure_ascii=False)}")
                lines.append(f"      label: {json.dumps(cat['label'], ensure_ascii=False)}")
                lines.append(f"      href: {json.dumps(cat['href'], ensure_ascii=False)}")
        else:
            lines.append("  categories: []")
    return "\n".join(lines) + ("\n" if lines else "")


def dump_project_yaml_items(items: list[dict]) -> str:
    """Serialize project listing items as YAML using Python stdlib json string quoting."""
    lines: list[str] = []
    for item in items:
        lines.append(f"- id: {json.dumps(item['id'], ensure_ascii=False)}")
        for field in ("title", "role", "funder", "period", "status", "url", "description"):
            lines.append(f"  {field}: {json.dumps(item[field], ensure_ascii=False)}")
        lines.append(f"  year: {item['year']}")
        if item["papers"]:
            lines.append("  papers:")
            for paper in item["papers"]:
                lines.append(f"    - key: {json.dumps(paper['key'], ensure_ascii=False)}")
                lines.append(f"      citation: {json.dumps(paper['citation'], ensure_ascii=False)}")
                lines.append(f"      title: {json.dumps(paper['title'], ensure_ascii=False)}")
                lines.append(f"      href: {json.dumps(paper['href'], ensure_ascii=False)}")
        else:
            lines.append("  papers: []")
    return "\n".join(lines) + ("\n" if lines else "")


def dump_research_interest_yaml_items(items: list[dict]) -> str:
    """Serialize research interest listing items as YAML using Python stdlib json string quoting."""
    lines: list[str] = []
    for item in items:
        lines.append(f"- id: {json.dumps(item['id'], ensure_ascii=False)}")
        lines.append(f"  title: {json.dumps(item['title'], ensure_ascii=False)}")
        lines.append(f"  description: {json.dumps(item['description'], ensure_ascii=False)}")
        if item["links"]:
            lines.append("  links:")
            for lk in item["links"]:
                lines.append(f"    - label: {json.dumps(lk['label'], ensure_ascii=False)}")
                lines.append(f"      href: {json.dumps(lk['href'], ensure_ascii=False)}")
        else:
            lines.append("  links: []")
        if item["papers"]:
            lines.append("  papers:")
            for paper in item["papers"]:
                lines.append(f"    - key: {json.dumps(paper['key'], ensure_ascii=False)}")
                for field in (
                    "year",
                    "pub_type",
                    "type_label",
                    "title_html",
                    "authors_html",
                    "venue_html",
                ):
                    lines.append(f"      {field}: {json.dumps(paper.get(field, ''), ensure_ascii=False)}")
                if paper["links"]:
                    lines.append("      links:")
                    for link in paper["links"]:
                        lines.append(f"        - label: {json.dumps(link['label'], ensure_ascii=False)}")
                        lines.append(f"          href: {json.dumps(link['href'], ensure_ascii=False)}")
                else:
                    lines.append("      links: []")
        else:
            lines.append("  papers: []")
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    bib_path = root / "data" / "references.bib"
    if not bib_path.is_file():
        raise FileNotFoundError(f"Missing bibliography file: {bib_path}")
    cat_path = root / "data" / "publication_categories.yml"
    if not cat_path.is_file():
        raise FileNotFoundError(f"Missing publication categories file: {cat_path}")
    proj_path = root / "data" / "projects.yml"
    if not proj_path.is_file():
        raise FileNotFoundError(f"Missing projects file: {proj_path}")
    interests_path = root / "data" / "research_interests.yml"
    if not interests_path.is_file():
        raise FileNotFoundError(f"Missing research interests file: {interests_path}")

    raw_bib = bib_path.read_text(encoding="utf8")
    entries = parse_bib(raw_bib)
    entries_by_key = {e["key"]: e for e in entries}
    bib_keys = set(entries_by_key)

    pub_categories = parse_publication_categories(
        cat_path.read_text(encoding="utf8"),
        valid_bib_keys=bib_keys,
    )
    projects = parse_projects(
        proj_path.read_text(encoding="utf8"),
        valid_bib_keys=bib_keys,
    )
    interests = parse_research_interests(
        interests_path.read_text(encoding="utf8"),
        valid_bib_keys=bib_keys,
    )

    published, preprints = build_listing_items(
        entries,
        pub_categories=pub_categories,
        relative_prefix="",
        warn_uncategorized=True,
    )

    out_dir = root / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "publications.yml").write_text(dump_yaml_items(published), encoding="utf8", newline="\n")
    (out_dir / "preprints.yml").write_text(dump_yaml_items(preprints), encoding="utf8", newline="\n")

    interest_items = build_research_interest_items(interests, entries_by_key)
    (out_dir / "research-interests.yml").write_text(
        dump_research_interest_yaml_items(interest_items), encoding="utf8", newline="\n"
    )

    for slug, _ in CATEGORIES:
        cat_pubs = build_category_publication_items(entries, pub_categories, slug)
        (out_dir / f"publications-{slug}.yml").write_text(
            dump_yaml_items(cat_pubs), encoding="utf8", newline="\n"
        )
        cat_projs = build_category_project_items(projects, entries_by_key, slug)
        (out_dir / f"projects-{slug}.yml").write_text(
            dump_project_yaml_items(cat_projs), encoding="utf8", newline="\n"
        )


if __name__ == "__main__":
    main()
