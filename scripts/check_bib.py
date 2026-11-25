"""Validate a BibTeX bibliography file for syntax, required fields, and duplicates."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from build_publications import (  # noqa: E402
    ALLOWED_TYPES,
    _normalize_doi,
    _read_balanced,
    tex_to_text,
)

_ENTRY_RE = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
_FIELD_RE = re.compile(r"\s*([A-Za-z_\-]+)\s*=\s*")
_META_TYPES = {"comment", "string", "preamble"}


def _has_stray_at(segment: str) -> bool:
    non_comment = "\n".join(
        line for line in segment.splitlines() if not line.lstrip().startswith("%")
    )
    return "@" in non_comment


def _normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFC", title).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def check_bib_text(text: str) -> list[str]:
    """Validate BibTeX text and return a list of human-readable error messages."""
    entries: list[dict] = []
    errors: list[str] = []
    pos = 0
    n = len(text)

    while pos < n:
        match = _ENTRY_RE.search(text, pos)
        if not match:
            if _has_stray_at(text[pos:]):
                errors.append("Malformed '@' entry header near end of BibTeX file")
            break

        if _has_stray_at(text[pos:match.start()]):
            errors.append(f"Malformed '@' entry header before @{match.group(1)}")

        raw_type = match.group(1)
        kind = raw_type.lower()
        open_ch = match.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        try:
            body, end_pos = _read_balanced(text, match.end(), open_ch, close_ch, f"@{raw_type}")
        except ValueError as exc:
            errors.append(str(exc))
            break
        pos = end_pos

        if kind in _META_TYPES:
            continue
        if kind not in ALLOWED_TYPES:
            errors.append(f"Unsupported BibTeX entry type @{raw_type}")
            continue

        comma_idx = body.find(",")
        if comma_idx < 0:
            errors.append(
                f"Missing comma after citation key in @{raw_type}{open_ch}{body[:40]}..."
            )
            continue
        key = body[:comma_idx].strip()
        if not key or any(ch.isspace() for ch in key):
            errors.append(f"Invalid or missing BibTeX citation key: {key!r}")
            continue

        rest = body[comma_idx + 1:]
        fields: dict[str, str] = {}
        fpos = 0
        rlen = len(rest)
        field_error = False

        while fpos < rlen:
            while fpos < rlen and (rest[fpos].isspace() or rest[fpos] == ","):
                fpos += 1
            if fpos >= rlen:
                break
            fm = _FIELD_RE.match(rest, fpos)
            if not fm:
                snippet = rest[fpos:fpos + 40].strip()
                errors.append(f"Malformed field syntax in {key}: {snippet!r}")
                field_error = True
                break
            name = fm.group(1).lower()
            p = fm.end()
            if p >= rlen:
                errors.append(f"Missing field value for {key}.{name}")
                field_error = True
                break

            if rest[p] == "{":
                try:
                    val, p = _read_balanced(rest, p + 1, "{", "}", f"{key}.{name}")
                except ValueError as exc:
                    errors.append(str(exc))
                    field_error = True
                    break
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
                    errors.append(f"Unclosed quoted string in {key}.{name}")
                    field_error = True
                    break
                val, p = rest[p + 1:q], q + 1
            else:
                vm = re.match(r"[^,]*", rest[p:])
                assert vm is not None
                val = vm.group(0).strip()
                p += vm.end()
                if not val:
                    errors.append(f"Empty bare field value in {key}.{name}")
                    field_error = True
                    break

            fields[name] = val.strip()
            while p < rlen and rest[p].isspace():
                p += 1
            if p < rlen:
                if rest[p] != ",":
                    errors.append(
                        f"Expected comma after {key}.{name}, found {rest[p:p + 20]!r}"
                    )
                    field_error = True
                    break
                p += 1
            fpos = p

        if field_error:
            continue

        entries.append({"key": key, "type": kind, "fields": fields})

    if not entries and not errors:
        return ["Bibliography contains no entries"]

    seen_keys: dict[str, str] = {}
    seen_titles: dict[str, str] = {}
    seen_dois: dict[str, str] = {}

    for entry in entries:
        key = entry["key"]
        fields = entry["fields"]

        key_cf = key.casefold()
        if key_cf in seen_keys:
            errors.append(
                f"Duplicate BibTeX citation key {key!r} (conflicts with {seen_keys[key_cf]!r})"
            )
        else:
            seen_keys[key_cf] = key

        title = tex_to_text(fields.get("title", ""))
        author = tex_to_text(fields.get("author", ""))
        year = fields.get("year", "").strip()

        if not title:
            errors.append(f"Missing required 'title' in {key}")
        else:
            norm_title = _normalize_title(title)
            if norm_title:
                if norm_title in seen_titles:
                    errors.append(
                        f"Duplicate normalized title in {key} (matches {seen_titles[norm_title]}): {title!r}"
                    )
                else:
                    seen_titles[norm_title] = key

        if not author:
            errors.append(f"Missing required 'author' in {key}")

        if not re.fullmatch(r"\d{4}", year) or not (2000 <= int(year) <= 2099):
            errors.append(
                f"Invalid 'year' {year!r} in {key}; expected 4-digit year in [2000, 2099]"
            )

        raw_doi = fields.get("doi", "").strip()
        if raw_doi:
            norm_doi = _normalize_doi(raw_doi).casefold()
            if norm_doi:
                if norm_doi in seen_dois:
                    errors.append(
                        f"Duplicate DOI in {key} (matches {seen_dois[norm_doi]}): {raw_doi!r}"
                    )
                else:
                    seen_dois[norm_doi] = key

    return errors


def check_bib_file(path: Path) -> list[str]:
    """Validate a BibTeX file at ``path`` and return a list of error messages."""
    bib_path = Path(path)
    if not bib_path.is_file():
        return [f"Bibliography file not found: {bib_path}"]
    text = bib_path.read_text(encoding="utf-8")
    return check_bib_text(text)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_bib = repo_root / "data" / "references.bib"

    parser = argparse.ArgumentParser(description="Validate a BibTeX bibliography file.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=default_bib,
        help="Path to the .bib file (default: data/references.bib)",
    )
    args = parser.parse_args(argv)

    errors = check_bib_file(args.path)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    print(f"OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
