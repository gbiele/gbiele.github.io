"""Tests for issue #5: CI checks (bibliography validator, internal link checker, workflow)."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from site_helpers import REPO_ROOT

CHECK_BIB_SCRIPT = REPO_ROOT / "scripts" / "check_bib.py"
CHECK_LINKS_SCRIPT = REPO_ROOT / "scripts" / "check_internal_links.py"
REFERENCES_BIB = REPO_ROOT / "data" / "references.bib"
WORKFLOW_YML = REPO_ROOT / ".github" / "workflows" / "publish.yml"


def _load_script(path: Path):
    assert path.is_file(), f"Expected script not found: {path}"
    spec = importlib.util.spec_from_file_location(f"issue5_{path.stem}", path)
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


def _load_workflow() -> dict:
    assert WORKFLOW_YML.is_file(), f"Workflow not found: {WORKFLOW_YML}"
    return yaml.safe_load(WORKFLOW_YML.read_text(encoding="utf8"))


def _workflow_triggers(wf: dict) -> dict:
    # PyYAML 1.1 parses bare `on:` as boolean True.
    triggers = wf.get("on")
    if triggers is None:
        triggers = wf.get(True)
    assert isinstance(triggers, dict), f"Expected workflow triggers mapping, got {type(triggers)}"
    return triggers


# =========================================================================
# 1. Bibliography Validator (scripts/check_bib.py)
# =========================================================================


def test_check_bib_valid_repo_file_and_cli_default():
    mod = _load_script(CHECK_BIB_SCRIPT)
    assert mod.check_bib_file(REFERENCES_BIB) == []
    assert mod.main([]) == 0

    proc = _run_script(CHECK_BIB_SCRIPT)
    assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip(), "Expected a short confirmation line on stdout"


def test_check_bib_missing_and_empty_inputs(tmp_path):
    mod = _load_script(CHECK_BIB_SCRIPT)

    missing = tmp_path / "does_not_exist.bib"
    assert mod.check_bib_file(missing), "Missing file must produce errors"
    assert mod.main([str(missing)]) == 1

    proc = _run_script(CHECK_BIB_SCRIPT, str(missing))
    assert proc.returncode == 1
    assert proc.stderr.strip(), "Expected error message on stderr for missing file"

    assert mod.check_bib_text(""), "Empty text must produce errors"
    assert mod.check_bib_text("% only comments\n% no entries\n"), "Comments-only text must produce errors"


@pytest.mark.parametrize(
    "label,bib_text",
    [
        (
            "unclosed_brace",
            "@article{Key2020,\n  title = {Unclosed title,\n  author = {Doe, Jane},\n  year = {2020}\n",
        ),
        (
            "unclosed_paren",
            "@article(Key2020,\n  title = {Title},\n  author = {Doe, Jane},\n  year = {2020}\n",
        ),
        (
            "unclosed_quote",
            '@article{Key2020,\n  title = "Unclosed quote,\n  author = {Doe, Jane},\n  year = {2020}\n}\n',
        ),
        (
            "stray_at",
            "@\n@article{Key2020,\n  title = {Title},\n  author = {Doe, Jane},\n  year = {2020}\n}\n",
        ),
        (
            "unsupported_entry_type",
            "@book{Key2020,\n  title = {A Book},\n  author = {Doe, Jane},\n  year = {2020}\n}\n",
        ),
        (
            "missing_comma_after_key",
            "@article{Key2020\n  title = {Title},\n  author = {Doe, Jane},\n  year = {2020}\n}\n",
        ),
        (
            "empty_citation_key",
            "@article{,\n  title = {Title},\n  author = {Doe, Jane},\n  year = {2020}\n}\n",
        ),
        (
            "whitespace_in_citation_key",
            "@article{Key 2020,\n  title = {Title},\n  author = {Doe, Jane},\n  year = {2020}\n}\n",
        ),
        (
            "malformed_field",
            "@article{Key2020,\n  title {Missing Equals},\n  author = {Doe, Jane},\n  year = {2020}\n}\n",
        ),
        (
            "missing_comma_between_fields",
            "@article{Key2020,\n  title = {Title}\n  author = {Doe, Jane},\n  year = {2020}\n}\n",
        ),
        (
            "empty_bare_field_value",
            "@article{Key2020,\n  title = {Title},\n  author = {Doe, Jane},\n  year = ,\n}\n",
        ),
    ],
)
def test_check_bib_parse_and_syntax_errors(label, bib_text):
    mod = _load_script(CHECK_BIB_SCRIPT)
    errors = mod.check_bib_text(bib_text)
    assert errors, f"Expected parse/syntax error for case {label!r}, got []"


def test_check_bib_ignores_meta_entries_and_allows_case_insensitive_types_and_fields():
    mod = _load_script(CHECK_BIB_SCRIPT)
    bib_text = (
        "% comment with @stray inside comment is fine\n"
        "@comment{ignored comment}\n"
        '@string{J = "Journal"}\n'
        '@preamble{"preamble"}\n'
        "@Article{Alpha2000,\n"
        "  Title = {First Study},\n"
        "  Author = {Doe, Jane},\n"
        "  Year = {2000}\n"
        "}\n"
        "@INPROCEEDINGS{Beta2099,\n"
        "  title = {Second Study},\n"
        "  author = {Roe, John},\n"
        "  year = 2099\n"
        "}\n"
    )
    assert mod.check_bib_text(bib_text) == []


def test_check_bib_duplicate_keys_exact_and_case_variant():
    mod = _load_script(CHECK_BIB_SCRIPT)

    exact_dup = (
        "@article{Biele2022,\n  title = {First Paper},\n  author = {Biele, Guido},\n  year = {2022}\n}\n"
        "@article{Biele2022,\n  title = {Second Paper},\n  author = {Biele, Guido},\n  year = {2023}\n}\n"
    )
    assert mod.check_bib_text(exact_dup), "Exact duplicate citation keys must fail"

    case_dup = (
        "@article{Biele2022,\n  title = {First Paper},\n  author = {Biele, Guido},\n  year = {2022}\n}\n"
        "@article{biele2022,\n  title = {Second Paper},\n  author = {Biele, Guido},\n  year = {2023}\n}\n"
    )
    assert mod.check_bib_text(case_dup), "Case-variant duplicate citation keys must fail"


def test_check_bib_duplicate_entries_by_normalized_title_and_doi():
    mod = _load_script(CHECK_BIB_SCRIPT)

    dup_title = (
        "@article{KeyA,\n"
        "  title = {A Study of {ADHD}: Caf{\\'e} Outcomes},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2021}\n"
        "}\n"
        "@misc{KeyB,\n"
        "  title = {a study of adhd café outcomes},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2021}\n"
        "}\n"
    )
    assert mod.check_bib_text(dup_title), "Duplicate normalized titles across keys must fail"

    dup_doi = (
        "@article{KeyC,\n"
        "  title = {Paper One},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2021},\n"
        "  doi = {https://doi.org/10.1000/ABC.123}\n"
        "}\n"
        "@article{KeyD,\n"
        "  title = {Completely Different Title},\n"
        "  author = {Biele, Guido},\n"
        "  year = {2022},\n"
        "  doi = {DOI:10.1000/abc.123}\n"
        "}\n"
    )
    assert mod.check_bib_text(dup_doi), "Duplicate normalized DOIs across keys must fail"


@pytest.mark.parametrize(
    "label,fields",
    [
        ("missing_title", "author = {Doe, Jane},\n  year = {2022}"),
        ("empty_title", "title = {},\n  author = {Doe, Jane},\n  year = {2022}"),
        ("missing_author", "title = {Some Title},\n  year = {2022}"),
        ("empty_author", "title = {Some Title},\n  author = {   },\n  year = {2022}"),
        ("missing_year", "title = {Some Title},\n  author = {Doe, Jane}"),
        ("empty_year", "title = {Some Title},\n  author = {Doe, Jane},\n  year = {}"),
        ("year_undated", "title = {Some Title},\n  author = {Doe, Jane},\n  year = {undated}"),
        ("year_nd", "title = {Some Title},\n  author = {Doe, Jane},\n  year = {n.d.}"),
        ("year_in_press", "title = {Some Title},\n  author = {Doe, Jane},\n  year = {in press}"),
        ("year_3_digits", "title = {Some Title},\n  author = {Doe, Jane},\n  year = {202}"),
        ("year_1999", "title = {Some Title},\n  author = {Doe, Jane},\n  year = {1999}"),
        ("year_2100", "title = {Some Title},\n  author = {Doe, Jane},\n  year = {2100}"),
    ],
)
def test_check_bib_required_fields_and_year_validation(label, fields):
    mod = _load_script(CHECK_BIB_SCRIPT)
    bib_text = f"@article{{Entry2022,\n  {fields}\n}}\n"
    errors = mod.check_bib_text(bib_text)
    assert errors, f"Expected validation error for {label!r}, got []"


def test_check_bib_reports_multiple_semantic_errors_together(tmp_path):
    mod = _load_script(CHECK_BIB_SCRIPT)
    bib_text = (
        "@article{NoYearKey,\n"
        "  title = {Has Title But No Year},\n"
        "  author = {Doe, Jane}\n"
        "}\n"
        "@article{NoTitleKey,\n"
        "  author = {Roe, John},\n"
        "  year = {2023}\n"
        "}\n"
    )
    errors = mod.check_bib_text(bib_text)
    assert len(errors) >= 2, f"Expected at least 2 errors reported together, got {errors!r}"
    joined = "\n".join(errors)
    assert "NoYearKey" in joined and "NoTitleKey" in joined

    bib_file = tmp_path / "bad.bib"
    bib_file.write_text(bib_text, encoding="utf8")
    proc = _run_script(CHECK_BIB_SCRIPT, str(bib_file))
    assert proc.returncode == 1
    assert "NoYearKey" in proc.stderr and "NoTitleKey" in proc.stderr


# =========================================================================
# 2. Internal Link Checker (scripts/check_internal_links.py)
# =========================================================================


def test_check_internal_links_valid_rendered_site(site):
    mod = _load_script(CHECK_LINKS_SCRIPT)
    assert mod.check_internal_links(site) == []
    assert mod.main([str(site)]) == 0

    proc = _run_script(CHECK_LINKS_SCRIPT, str(site))
    assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip(), "Expected confirmation line on stdout"


def test_check_internal_links_missing_or_empty_site_dir(tmp_path):
    mod = _load_script(CHECK_LINKS_SCRIPT)

    missing = tmp_path / "no_such_site"
    assert mod.check_internal_links(missing), "Missing site dir must fail"
    assert mod.main([str(missing)]) == 1

    empty = tmp_path / "empty_site"
    empty.mkdir()
    (empty / "readme.txt").write_text("no html here", encoding="utf8")
    assert mod.check_internal_links(empty), "Site dir with no .html files must fail"
    assert mod.main([str(empty)]) == 1


def test_check_internal_links_detects_broken_links_and_assets(tmp_path):
    mod = _load_script(CHECK_LINKS_SCRIPT)
    site_dir = tmp_path / "site"
    (site_dir / "sub").mkdir(parents=True)
    (site_dir / "ok.png").write_bytes(b"png")
    (site_dir / "empty_dir").mkdir()

    (site_dir / "index.html").write_text(
        "<html><head>"
        '<link rel="stylesheet" href="css/missing.css">'
        '<script src="js/missing.js"></script>'
        "</head><body>"
        '<a href="missing.html">broken rel</a>'
        '<a href="/missing/page.html">broken root-rel</a>'
        '<img src="images/nope.png">'
        '<img srcset="ok.png 1x, missing.png 2x">'
        '<a href="empty_dir/">dir without index</a>'
        '<a href="/no_such_dir">missing dir</a>'
        "</body></html>",
        encoding="utf8",
    )
    (site_dir / "sub" / "page.html").write_text(
        '<html><body><a href="../missing_up.html">broken up</a></body></html>',
        encoding="utf8",
    )

    errors = mod.check_internal_links(site_dir)
    expected_urls = [
        "css/missing.css",
        "js/missing.js",
        "missing.html",
        "/missing/page.html",
        "images/nope.png",
        "missing.png",
        "empty_dir/",
        "/no_such_dir",
        "../missing_up.html",
    ]
    assert len(errors) >= len(expected_urls), f"Expected all broken links reported, got: {errors}"
    joined = "\n".join(errors)
    for url in expected_urls:
        assert url in joined, f"Expected broken URL {url!r} in errors:\n{joined}"
    assert any("index.html" in e for e in errors)
    assert any("sub/page.html" in e or "sub\\page.html" in e for e in errors)


def test_check_internal_links_rejects_path_traversal(tmp_path):
    mod = _load_script(CHECK_LINKS_SCRIPT)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf8")

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(
        "<html><body>"
        '<a href="../outside.txt">literal traversal</a>'
        '<a href="%2e%2e/outside.txt">encoded traversal</a>'
        "</body></html>",
        encoding="utf8",
    )

    errors = mod.check_internal_links(site_dir)
    assert len(errors) >= 2, f"Both literal and percent-encoded traversals must fail, got: {errors}"


def test_check_internal_links_accepts_valid_forms_and_ignores_external_schemes(tmp_path):
    mod = _load_script(CHECK_LINKS_SCRIPT)
    site_dir = tmp_path / "site"
    (site_dir / "research").mkdir(parents=True)
    (site_dir / "files").mkdir(parents=True)
    (site_dir / "sub").mkdir(parents=True)

    (site_dir / "research" / "index.html").write_text("<html><body>Research</body></html>", encoding="utf8")
    (site_dir / "publications.html").write_text("<html><body>Pubs</body></html>", encoding="utf8")
    (site_dir / "files" / "my doc.pdf").write_bytes(b"%PDF-1.4")
    (site_dir / "pic.png").write_bytes(b"png")

    # Page in directory without index.html using a query-only self link
    (site_dir / "sub" / "view.html").write_text(
        '<html><body><a href="?category=adhd">filter</a></body></html>',
        encoding="utf8",
    )

    (site_dir / "index.html").write_text(
        "<html><body>"
        '<a href="publications.html?category=adhd#published">pubs</a>'
        '<a href="/publications.html">root-rel</a>'
        '<a href="files/my%20doc.pdf">encoded</a>'
        '<a href="research/">dir slash</a>'
        '<a href="/research">root dir</a>'
        '<a href="http://example.com/404">http</a>'
        '<a href="https://example.com/404">https</a>'
        '<a href="mailto:test@example.com">mail</a>'
        '<a href="tel:+4712345678">tel</a>'
        '<a href="javascript:void(0)">js</a>'
        '<a href="//example.com/proto">proto-rel</a>'
        '<a href="#section">fragment</a>'
        '<a href="">empty</a>'
        '<img src="data:image/png;base64,iVBORw0KGgo=">'
        '<img srcset="data:image/svg+xml;utf8,<svg></svg>,extra 1x, pic.png 2x">'
        "</body></html>",
        encoding="utf8",
    )

    assert mod.check_internal_links(site_dir) == []


# =========================================================================
# 3. GitHub Actions Workflow (.github/workflows/publish.yml)
# =========================================================================


def test_workflow_triggers_and_deploy_gating_preserved():
    wf = _load_workflow()
    triggers = _workflow_triggers(wf)

    assert triggers["push"]["branches"] == ["quarto", "master"]
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers

    deploy = wf["jobs"]["deploy"]
    needs = deploy["needs"]
    assert needs == "render" or needs == ["render"]
    assert deploy["if"] == "github.ref == 'refs/heads/master' && vars.PAGES_DEPLOY == 'true'"


def test_workflow_action_versions_updated():
    wf = _load_workflow()
    render_uses = [s.get("uses", "") for s in wf["jobs"]["render"]["steps"] if "uses" in s]
    deploy_uses = [s.get("uses", "") for s in wf["jobs"]["deploy"]["steps"] if "uses" in s]

    assert "actions/checkout@v7" in render_uses
    assert not any(u.startswith("actions/checkout@v4") for u in render_uses)

    assert "actions/setup-python@v7" in render_uses

    assert "actions/upload-pages-artifact@v5" in render_uses
    assert not any(u.startswith("actions/upload-pages-artifact@v3") for u in render_uses)

    assert "actions/deploy-pages@v5" in deploy_uses
    assert not any(u.startswith("actions/deploy-pages@v4") for u in deploy_uses)


def test_workflow_render_job_failing_checks_and_order():
    wf = _load_workflow()
    steps = wf["jobs"]["render"]["steps"]

    def _find_idx(predicate, description):
        matches = [i for i, s in enumerate(steps) if predicate(s)]
        assert matches, f"Missing expected step in render job: {description}"
        return matches[0]

    bib_idx = _find_idx(lambda s: "scripts/check_bib.py" in s.get("run", ""), "scripts/check_bib.py")
    quarto_idx = _find_idx(lambda s: "quarto render" in s.get("run", ""), "quarto render")
    links_idx = _find_idx(
        lambda s: "scripts/check_internal_links.py" in s.get("run", ""),
        "scripts/check_internal_links.py",
    )
    def _runs_pytest(step: dict) -> bool:
        for line in step.get("run", "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.search(r"\bpytest\b", stripped) and "pip install" not in stripped:
                return True
        return False

    pytest_idx = _find_idx(_runs_pytest, "pytest execution")
    upload_idx = _find_idx(
        lambda s: s.get("uses", "").startswith("actions/upload-pages-artifact@"),
        "actions/upload-pages-artifact",
    )

    assert quarto_idx < links_idx, "Internal link check must run after quarto render"
    assert bib_idx < upload_idx, "Bib check must run before upload-pages-artifact"
    assert links_idx < upload_idx, "Internal link check must run before upload-pages-artifact"
    assert pytest_idx < upload_idx, "pytest must run before upload-pages-artifact"

    for idx, name in [(bib_idx, "check_bib"), (links_idx, "check_internal_links"), (pytest_idx, "pytest")]:
        assert steps[idx].get("continue-on-error") is not True, (
            f"{name} step must fail the job (must not set continue-on-error: true)"
        )

    all_runs = "\n".join(s.get("run", "") for s in steps).lower()
    for pkg in ("pytest", "pyyaml", "beautifulsoup4", "pypdf"):
        assert pkg in all_runs, f"Render job must install test dependency {pkg!r}"


def test_workflow_external_link_check_with_summary_non_blocking():
    wf = _load_workflow()
    steps = wf["jobs"]["render"]["steps"]

    quarto_steps = [s for s in steps if "quarto render" in s.get("run", "")]
    assert quarto_steps, "Missing quarto render step"
    quarto_id = quarto_steps[0].get("id")
    assert quarto_id, "quarto render step must define an id so lychee can check its outcome"

    lychee_steps = [s for s in steps if s.get("uses") == "lycheeverse/lychee-action@v2"]
    assert lychee_steps, "Missing lycheeverse/lychee-action@v2 step in render job"
    lychee = lychee_steps[0]

    lychee_with = lychee.get("with", {})
    args = str(lychee_with.get("args", ""))
    assert "_site" in args, f"lychee args must target _site, got: {args!r}"
    assert re.search(r"--scheme[\s=]+http\b", args), f"lychee args must include --scheme http: {args!r}"
    assert re.search(r"--scheme[\s=]+https\b", args), f"lychee args must include --scheme https: {args!r}"

    assert lychee_with.get("fail") in (False, "false"), f"lychee must set fail: false, got: {lychee_with.get('fail')!r}"
    assert lychee_with.get("jobSummary") in (True, "true"), (
        f"lychee must set jobSummary: true, got: {lychee_with.get('jobSummary')!r}"
    )

    cond = str(lychee.get("if", ""))
    assert cond, "lychee step must have an `if:` condition"
    assert re.search(
        rf"steps\.{re.escape(quarto_id)}\.(?:outcome|conclusion)\s*==\s*['\"]success['\"]",
        cond,
    ), f"lychee `if:` must require quarto render step ({quarto_id}) to succeed, got: {cond!r}"
    assert "always()" in cond or "failure()" in cond, (
        f"lychee `if:` must still run when earlier checks fail (always() or failure()), got: {cond!r}"
    )
