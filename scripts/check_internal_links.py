"""Check rendered HTML files in a site directory for broken internal links."""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _parse_srcset(value: str) -> list[str]:
    """Extract candidate URLs from an HTML srcset attribute without splitting data: URIs."""
    urls: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        while i < n and (value[i].isspace() or value[i] == ","):
            i += 1
        if i >= n:
            break

        start = i
        if value[i:i + 5].lower() == "data:":
            while i < n and not value[i].isspace():
                i += 1
        else:
            while i < n and not value[i].isspace() and value[i] != ",":
                i += 1
        url = value[start:i]
        if url:
            urls.append(url)

        while i < n and value[i] != ",":
            i += 1
        if i < n and value[i] == ",":
            i += 1
    return urls


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for attr, value in attrs:
            if value is None:
                continue
            attr_lower = attr.lower()
            if attr_lower in ("href", "src"):
                self.urls.append(value)
            elif attr_lower == "srcset":
                self.urls.extend(_parse_srcset(value))


def _is_internal(url: str) -> bool:
    cleaned = url.strip()
    if not cleaned or cleaned.startswith("#") or cleaned.startswith("//"):
        return False
    if _SCHEME_RE.match(cleaned):
        return False
    return True


def _resolve_internal(site_root: Path, page: Path, url: str) -> bool:
    cleaned = url.strip()
    path_part = cleaned.split("#", 1)[0].split("?", 1)[0]
    if not path_part:
        return True

    trailing_slash = path_part.endswith("/")
    decoded = urllib.parse.unquote(path_part).replace("\\", "/")

    if decoded.startswith("/"):
        candidate = site_root / decoded.lstrip("/")
    else:
        candidate = page.parent / decoded

    site_root_resolved = site_root.resolve()
    target_norm = Path(os.path.normpath(str(candidate)))
    target_resolved = target_norm.resolve()

    if not target_resolved.is_relative_to(site_root_resolved):
        return False

    if trailing_slash or target_resolved.is_dir():
        target_resolved = (target_resolved / "index.html").resolve()
        if not target_resolved.is_relative_to(site_root_resolved):
            return False

    return target_resolved.is_file()


def check_internal_links(site_dir: Path) -> list[str]:
    """Check all HTML files under ``site_dir`` for broken internal links."""
    root = Path(site_dir)
    if not root.is_dir():
        return [f"Site directory not found: {root}"]

    html_files = sorted(root.rglob("*.html"))
    if not html_files:
        return [f"Site directory contains no .html files: {root}"]

    errors: list[str] = []
    for page in html_files:
        extractor = _LinkExtractor()
        extractor.feed(page.read_text(encoding="utf-8", errors="replace"))
        extractor.close()

        rel_page = page.relative_to(root).as_posix()
        for url in extractor.urls:
            if not _is_internal(url):
                continue
            if not _resolve_internal(root, page, url):
                errors.append(f"{rel_page}: broken internal link {url!r}")

    return errors


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_site = repo_root / "_site"

    parser = argparse.ArgumentParser(
        description="Check rendered HTML files for broken internal links."
    )
    parser.add_argument(
        "site_dir",
        nargs="?",
        type=Path,
        default=default_site,
        help="Path to rendered site directory (default: _site)",
    )
    args = parser.parse_args(argv)

    errors = check_internal_links(args.site_dir)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    print(f"OK: {args.site_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
