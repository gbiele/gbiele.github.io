"""Helpers shared by the site tests (no dependency on the site's own code)."""

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = REPO_ROOT / "_site"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legacy_site.json"

RESEARCH_SLUGS = ["epi", "nfrsea", "bayes", "causalinf", "dm", "decneurosc", "cogneuro"]
RESEARCH_ORDER = ["nfrsea", "epi", "bayes", "causalinf", "dm", "decneurosc", "cogneuro"]


def load_legacy():
    return json.loads(FIXTURE.read_text(encoding="utf8"))


def norm(text):
    """Contract normalisation: casefold, keep only Unicode letters and digits."""
    return "".join(c for c in text.casefold() if c.isalnum())


def collapse_ws(text):
    return " ".join(text.split())


def soup_of(path):
    return BeautifulSoup(Path(path).read_text(encoding="utf8", errors="replace"), "html.parser")


def page_text(path):
    soup = soup_of(path)
    body = soup.body or soup
    return collapse_ws(body.get_text(" "))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def is_internal(url):
    """Relative or root-relative URL (not http(s):, mailto:, #, data:, //host)."""
    url = url.strip()
    if not url or url.startswith("#") or url.startswith("//"):
        return False
    return not _SCHEME.match(url)


def resolve(site, page, url):
    """Resolve an internal URL found on `page` to a filesystem path under `site`.

    Strips ?query and #fragment; a path ending in '/' (or naming a directory)
    resolves to its index.html. Backslashes are treated as '/'.
    """
    url = url.strip().replace("\\", "/")
    url = url.split("#", 1)[0].split("?", 1)[0]
    url = unquote(url)
    if url.startswith("/"):
        target = os.path.join(str(site), url.lstrip("/"))
    else:
        target = os.path.join(str(Path(page).parent), url)
    target = Path(os.path.normpath(target))
    if url.endswith("/") or url == "" or target.is_dir():
        target = target / "index.html"
    return target


def urls_in_tag(tag):
    out = []
    for attr in ("href", "src"):
        val = tag.get(attr)
        if isinstance(val, str):
            out.append(val)
    srcset = tag.get("srcset")
    if isinstance(srcset, str):
        for part in srcset.split(","):
            part = part.strip()
            if part:
                out.append(part.split()[0])
    return out


def internal_urls(path):
    soup = soup_of(path)
    urls = []
    for tag in soup.find_all(True):
        urls.extend(u for u in urls_in_tag(tag) if is_internal(u))
    return urls


_META_URL = re.compile(r"url\s*=\s*['\"]?([^'\"]+)['\"]?", re.I)
_JS_PATTERNS = [
    re.compile(r"location(?:\.href)?\s*=\s*(['\"])(.+?)\1"),
    re.compile(r"location\.(?:replace|assign)\(\s*(['\"])(.+?)\1"),
]
# Quarto alias pages: `var redirects = {"": "..\\..\\research\\epi.html", ...}`
_JS_REDIRECT_MAP = re.compile(r"\bredirects\s*=\s*\{(.*?)\}", re.S)
_JS_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _js_unescape(s):
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return s


def redirect_targets(path):
    """Target URLs of meta refresh tags, window.location scripts and Quarto
    `redirects = {...}` maps (all values of the map)."""
    soup = soup_of(path)
    targets = []
    for meta in soup.find_all("meta"):
        if str(meta.get("http-equiv", "")).lower() == "refresh":
            m = _META_URL.search(str(meta.get("content", "")))
            if m:
                targets.append(m.group(1).strip())
    for script in soup.find_all("script"):
        code = script.string or script.get_text() or ""
        for pat in _JS_PATTERNS:
            targets.extend(m.group(2) for m in pat.finditer(code))
        for block in _JS_REDIRECT_MAP.finditer(code):
            targets.extend(_js_unescape(m.group(2)) for m in _JS_STRING.finditer(block.group(1)))
    return targets


def redirects_to(site, page, expected):
    """True if `page` redirects to the existing file `expected` (both under site)."""
    expected = Path(os.path.normpath(str(expected)))
    if not expected.is_file():
        return False
    for url in redirect_targets(page):
        if not is_internal(url.replace("\\", "/")):
            continue
        if resolve(site, page, url) == expected:
            return True
    return False


# --- minimal BibTeX reader (fields with balanced braces or quotes) ---------

_ENTRY = re.compile(r"@\s*([A-Za-z]+)\s*[{(]")


def _read_balanced(text, i, open_ch, close_ch):
    depth = 1
    j = i
    while j < len(text) and depth:
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch:
            depth -= 1
        j += 1
    return text[i:j - 1], j


def parse_bib(text):
    entries = []
    for m in _ENTRY.finditer(text):
        kind = m.group(1).lower()
        if kind in ("comment", "string", "preamble"):
            continue
        body, _ = _read_balanced(text, m.end(), "{", "}")
        fields = {}
        k = body.find(",")
        rest = body[k + 1:] if k >= 0 else ""
        pos = 0
        fm = re.compile(r"\s*([A-Za-z_\-]+)\s*=\s*")
        while pos < len(rest):
            mm = fm.match(rest, pos)
            if not mm:
                pos += 1
                continue
            name = mm.group(1).lower()
            p = mm.end()
            if p < len(rest) and rest[p] == "{":
                val, p = _read_balanced(rest, p + 1, "{", "}")
            elif p < len(rest) and rest[p] == '"':
                q = p + 1
                depth = 0
                while q < len(rest) and not (rest[q] == '"' and depth == 0):
                    depth += {"{": 1, "}": -1}.get(rest[q], 0)
                    q += 1
                val, p = rest[p + 1:q], q + 1
            else:
                vm = re.match(r"[^,]*", rest[p:])
                val, p = vm.group(0).strip(), p + vm.end()
            fields[name] = val
            nxt = rest.find(",", p)
            pos = len(rest) if nxt < 0 else nxt + 1
        entries.append({"type": kind, "fields": fields})
    return entries


def perfect_matching(left, right, ok):
    """Size of a maximum bipartite matching between `left` and `right`."""
    match_r = {}

    def try_assign(i, seen):
        for j in range(len(right)):
            if ok(left[i], right[j]) and j not in seen:
                seen.add(j)
                if j not in match_r or try_assign(match_r[j], seen):
                    match_r[j] = i
                    return True
        return False

    return sum(1 for i in range(len(left)) if try_assign(i, set()))
