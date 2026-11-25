"""Held-out tests for issue #5: independent edge cases under the same contract."""

import importlib.util
import subprocess
import sys
from pathlib import Path

from site_helpers import REPO_ROOT

CHECK_BIB_SCRIPT = REPO_ROOT / "scripts" / "check_bib.py"
CHECK_LINKS_SCRIPT = REPO_ROOT / "scripts" / "check_internal_links.py"


def _load_script(path: Path):
    assert path.is_file(), f"Expected script not found: {path}"
    spec = importlib.util.spec_from_file_location(f"issue5_heldout_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_script(path: Path, *args: str) -> subprocess.CompletedProcess:
    assert path.is_file(), f"Expected script not found: {path}"
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf8",
        errors="replace",
    )


def test_heldout_bib_parenthesized_entries_and_latex_doi_normalization():
    mod = _load_script(CHECK_BIB_SCRIPT)

    valid_paren = (
        "@preamble{\"hello\"}\n"
        "@string{PUB = \"Press\"}\n"
        "@Unpublished(ParenKey2024,\n"
        "  TITLE = {Working Paper on {\\AA}ngstr{\\\"o}m},\n"
        "  AUTHOR = {Biele, Guido},\n"
        "  YEAR = {2024}\n"
        ")\n"
    )
    assert mod.check_bib_text(valid_paren) == []

    dup_latex_title = (
        "@article(KeyOne,\n"
        "  title = {{\\\"O}konomie: A Review!},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2020}\n"
        ")\n"
        "@techreport{KeyTwo,\n"
        "  title = {ökonomie a review},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2021}\n"
        "}\n"
    )
    assert mod.check_bib_text(dup_latex_title), "Normalized LaTeX diacritic title duplicates must fail"

    dup_dx_doi = (
        "@article{KeyThree,\n"
        "  title = {Unique Title Alpha},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2020},\n"
        "  doi = {http://dx.doi.org/10.1234/Test-99}\n"
        "}\n"
        "@article{KeyFour,\n"
        "  title = {Unique Title Beta},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2021},\n"
        "  doi = {10.1234/test-99}\n"
        "}\n"
    )
    assert mod.check_bib_text(dup_dx_doi), "Duplicate DOI with http://dx.doi.org/ prefix must fail"


def test_heldout_bib_latex_only_empty_fields_and_multiple_errors():
    mod = _load_script(CHECK_BIB_SCRIPT)

    empty_after_latex = (
        "@article{EmptyLatexTitle,\n"
        "  title = {{}},\n"
        "  author = {Doe, Jane},\n"
        "  year = {2022}\n"
        "}\n"
        "@misc{BadYearAndAuthor,\n"
        "  title = {Valid Title},\n"
        "  author = {{   }},\n"
        "  year = {1998}\n"
        "}\n"
    )
    errors = mod.check_bib_text(empty_after_latex)
    assert len(errors) >= 3, f"Expected errors for empty title, empty author, and out-of-range year, got: {errors}"


def test_heldout_internal_links_nested_dirs_srcset_and_encoded_traversal(tmp_path):
    mod = _load_script(CHECK_LINKS_SCRIPT)

    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf8")

    site_dir = tmp_path / "nested_site"
    (site_dir / "a" / "b").mkdir(parents=True)
    (site_dir / "a" / "c").mkdir(parents=True)
    (site_dir / "a" / "c" / "target.html").write_text("<html><body>OK</body></html>", encoding="utf8")
    (site_dir / "a" / "b" / "img.png").write_bytes(b"png")

    # Valid nested page with relative link, query-only link (no index.html in a/b), and data-URI srcset with comma
    (site_dir / "a" / "b" / "page.html").write_text(
        "<html><body>"
        '<a href="../c/target.html?x=1#top">valid rel</a>'
        '<a href="?filter=active">query only</a>'
        '<img srcset="data:image/svg+xml;base64,PHN2Zz4=,ignored 1x, img.png 2x">'
        "</body></html>",
        encoding="utf8",
    )
    assert mod.check_internal_links(site_dir) == []

    # Add broken links: percent-encoded traversal out of nested dir + broken srcset candidate
    (site_dir / "a" / "b" / "broken.html").write_text(
        "<html><body>"
        '<a href="%2e%2e/%2e%2e/%2e%2e/secret.txt">escape</a>'
        '<img srcset="img.png 1x, nope.png 2x">'
        "</body></html>",
        encoding="utf8",
    )
    errors = mod.check_internal_links(site_dir)
    assert len(errors) >= 2, f"Expected both traversal and broken srcset reported, got: {errors}"
    joined = "\n".join(errors)
    assert "secret.txt" in joined
    assert "nope.png" in joined
    assert "broken.html" in joined


def test_heldout_internal_links_cli_exit_codes_and_output(tmp_path):
    valid_site = tmp_path / "ok_site"
    valid_site.mkdir()
    (valid_site / "index.html").write_text('<html><body><a href="other.html">ok</a></body></html>', encoding="utf8")
    (valid_site / "other.html").write_text("<html><body>other</body></html>", encoding="utf8")

    proc_ok = _run_script(CHECK_LINKS_SCRIPT, str(valid_site))
    assert proc_ok.returncode == 0, f"Expected exit 0, got {proc_ok.returncode}: {proc_ok.stderr}"
    assert proc_ok.stdout.strip()

    broken_site = tmp_path / "bad_site"
    broken_site.mkdir()
    (broken_site / "index.html").write_text(
        '<html><body><a href="gone1.html">1</a><a href="gone2.html">2</a></body></html>',
        encoding="utf8",
    )
    proc_bad = _run_script(CHECK_LINKS_SCRIPT, str(broken_site))
    assert proc_bad.returncode == 1
    assert "gone1.html" in proc_bad.stderr and "gone2.html" in proc_bad.stderr
