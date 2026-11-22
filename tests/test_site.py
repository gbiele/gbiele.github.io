"""Tests for issue #1: the website rebuilt as a Quarto project (contract Part A).

Expected values come from the old Hugo output (captured in
tests/fixtures/legacy_site.json and the sentences hard-coded below) and from
the contract.
"""

import re
import subprocess
import unicodedata

import pytest
import yaml

from site_helpers import (
    REPO_ROOT, RESEARCH_ORDER, RESEARCH_SLUGS, collapse_ws, internal_urls, norm,
    page_text, parse_bib, redirects_to, resolve, sha256, soup_of,
)

# --- 1. render ---------------------------------------------------------------


def test_render_produces_index(site):
    assert (REPO_ROOT / "_quarto.yml").is_file()
    assert (site / "index.html").is_file()


# --- 2. about page -----------------------------------------------------------

ABOUT_STRINGS = [
    "Guido Biele",
    "Norwegian Institute of Public Health",
    "I am a research professor in the Department of Child Health and Development "
    "at the Norwegian Institute of Public Health",
    "My background is in experimental psychology and cognitive neuroscience",
    "Statistical modelling",
    "Bayesian statistics",
    "Cognitive modelling",
    "ADHD",
    "PhD, 2005",
    "Max Planck Institute for Human Development, Berlin",
    "Diploma (Psychology), 1998",
    "Free University Berlin",
]


@pytest.mark.parametrize("text", ABOUT_STRINGS)
def test_about_page_contains_text(site, text):
    assert text in page_text(site / "index.html")


def test_about_page_shows_old_avatar(site, legacy):
    page = site / "index.html"
    hashes = []
    for img in soup_of(page).find_all("img"):
        src = img.get("src") or ""
        target = resolve(site, page, src)
        if target.is_file():
            hashes.append(sha256(target))
    assert legacy["avatar_sha256"] in hashes, "no <img> on the about page shows the old avatar"


def test_about_page_links_current_cv_pdf(site, legacy):
    page = site / "index.html"
    for a in soup_of(page).find_all("a", href=True):
        href = a["href"]
        if href.lower().split("?")[0].endswith(".pdf"):
            target = resolve(site, page, href)
            if target.is_file() and sha256(target) != legacy["cv_sha256"]:
                return
    pytest.fail("no link on the about page resolves to an updated CV PDF")


@pytest.mark.parametrize("fragment", [
    "scholar.google.com/citations?user=0UkWz9IAAAAJ",
    "github.com/gbiele",
    "discourse.mc-stan.org/u/guido_biele",
    "mailto:guido.biele@fhi.no",
])
def test_about_page_has_contact_link(site, fragment):
    hrefs = [a["href"] for a in soup_of(site / "index.html").find_all("a", href=True)]
    assert any(fragment in h for h in hrefs), f"no link containing {fragment!r}"


# --- 3. research pages -------------------------------------------------------

OLD_TITLES = {
    "epi": "ADHD risk factors",
    "nfrsea": "Effects of stimulant medication and special education on school performance",
    "bayes": "Bayesian statistics",
    "causalinf": "Causal inference from observational data",
    "dm": "Decision making",
    "decneurosc": "Decision neuroscience",
    "cogneuro": "Cognitive neuroscience",
}

# One plain-text sentence (no links/footnotes inside) per page, taken from
# div.article-style of the old project/<slug>/index.html.
OLD_SENTENCES = {
    "epi": "In comparison, inverse probability of participation weights will reduce bias.",
    "nfrsea": "Estimating causal effects from observational data is hard, some would say "
              "rarely possible at all.",
    "bayes": "Statistical inference is at the core of scientific research because it allows "
             "us to learn in a systematic manner from data.",
    "causalinf": "The thematic focus of this research is on the effect of medication and special "
                 "education on educational and other outcomes of children with ADHD.",
    "dm": "In hindsight it is not surprising that reciprocity explained behavior better.",
    "decneurosc": "We also investigate how learning and decision making processes are different "
                  "in adults with ADHD.",
    "cogneuro": "In this research line I used and sometimes still use mathematical modelling of "
                "cognitive processes, functional magnetic resonance imaging (fMRI), and EEG to "
                "understand how humans make decisions, and how decision processes are different "
                "in adults with ADHD.",
}


@pytest.mark.parametrize("slug", RESEARCH_SLUGS)
def test_research_page_title(site, slug):
    page = site / "research" / f"{slug}.html"
    assert page.is_file()
    title = soup_of(page).title
    assert title is not None and OLD_TITLES[slug] in collapse_ws(title.get_text())


@pytest.mark.parametrize("slug", RESEARCH_SLUGS)
def test_research_page_contains_old_sentence(site, slug):
    page = site / "research" / f"{slug}.html"
    assert page.is_file()
    assert OLD_SENTENCES[slug] in page_text(page)


@pytest.mark.parametrize("slug", RESEARCH_SLUGS)
def test_research_page_images_resolve(site, slug):
    page = site / "research" / f"{slug}.html"
    assert page.is_file()
    imgs = soup_of(page).find_all("img")
    if slug in ("epi", "nfrsea", "bayes"):
        # Edge case in the contract: the old pages had images; they must work.
        assert imgs, f"research/{slug}.html has no images"
    for img in imgs:
        src = img.get("src") or ""
        assert src, f"<img> without src on research/{slug}.html"
        assert resolve(site, page, src).is_file(), f"broken image {src!r} on research/{slug}.html"


def test_research_index_links_pages_in_order(site):
    page = site / "research" / "index.html"
    assert page.is_file()
    soup = soup_of(page)
    scope = soup.find("main") or soup.body or soup
    wanted = {(site / "research" / f"{s}.html").resolve(): s for s in RESEARCH_SLUGS}
    seen = []
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if not href.strip() or href.startswith("#") or re.match(r"^[a-zA-Z][\w+.\-]*:", href):
            continue
        slug = wanted.get(resolve(site, page, href).resolve())
        if slug and slug not in seen:
            seen.append(slug)
    assert seen == RESEARCH_ORDER


# --- 4. publications page ----------------------------------------------------


def _pub_entries(site):
    page = site / "publications.html"
    assert page.is_file()
    return [norm(d.get_text(" ")) for d in soup_of(page).select("article.pub-entry .pub-title")]


def test_publications_page_has_69_entries(site):
    assert len(_pub_entries(site)) == 69


def test_publications_page_lists_every_old_title(site, legacy):
    entries = _pub_entries(site)
    missing = [p["pub_id"] for p in legacy["publications"]
               if not any(norm(p["title"]) in e for e in entries)]
    assert not missing, f"no pub-entry for: {missing}"


# --- 5. references bib -------------------------------------------------------


_LATEX_LIGATURES = {
    "aa": "å", "AA": "Å", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ",
    "ss": "ß", "l": "ł", "L": "Ł", "i": "i", "j": "j",
}
_LATEX_ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
    "c": "\u0327", "r": "\u030a",
}


def _latex_norm(text):
    text = text.replace("\\textbackslashtextquoteright", "'")
    for k, v in _LATEX_LIGATURES.items():
        text = re.sub(rf"\{{\\{k}\}}|\\{k}(?![A-Za-z])", v, text)
    text = re.sub(
        r"\{\\(['`^\"~=.]|[uvHcr])\{?([A-Za-z])\}?\}|\\(['`^\"~=.]|[uvHcr])\{([A-Za-z])\}|\\(['`^~=.])([A-Za-z])",
        lambda m: unicodedata.normalize(
            "NFC",
            (m.group(2) or m.group(4) or m.group(6))
            + _LATEX_ACCENTS[m.group(1) or m.group(3) or m.group(5)],
        ),
        text,
    )
    text = re.sub(r"\\(?:leq|le|geq|ge|pm|times|cdot)\b", "", text)
    text = re.sub(r"\\mu\b|[\u03bc\u00b5]", "mu", text)
    text = re.sub(r"\\[A-Za-z]+\*?", "", text)
    return norm(text)


def _bib():
    assert not (REPO_ROOT / "data" / "legacy-publications.bib").exists(), (
        "data/legacy-publications.bib must be removed"
    )
    path = REPO_ROOT / "data" / "references.bib"
    assert path.is_file(), "data/references.bib missing"
    return parse_bib(path.read_text(encoding="utf8"))


def test_bib_has_69_entries():
    assert len(_bib()) == 69


def test_legacy_bib_keeps_abstracts(legacy):
    entries = _bib()
    bad = []
    for p in legacy["publications"]:
        if p["abstract_start"] is None:
            continue
        match = [e for e in entries if _latex_norm(e["fields"].get("title", "")) == _latex_norm(p["title"])]
        if not any(_latex_norm(e["fields"].get("abstract", "")).startswith(_latex_norm(p["abstract_start"]))
                   for e in match):
            bad.append(p["pub_id"])
    assert not bad, f"missing/incomplete abstract for: {bad}"


# --- 6. internal links -------------------------------------------------------


def test_no_broken_internal_links(site):
    broken = []
    for page in site.rglob("*.html"):
        for url in internal_urls(page):
            if not resolve(site, page, url).is_file():
                broken.append(f"{page.relative_to(site).as_posix()}: {url}")
    assert not broken, f"{len(broken)} broken links, e.g.\n" + "\n".join(broken[:30])


# --- 7. old URLs ---------------------------------------------------------------


@pytest.mark.parametrize("slug", RESEARCH_SLUGS)
def test_old_project_url_redirects(site, slug):
    page = site / "project" / slug / "index.html"
    assert page.is_file()
    assert redirects_to(site, page, site / "research" / f"{slug}.html")


def test_old_publication_urls_redirect(site, legacy):
    bad = []
    for p in legacy["publications"]:
        page = site / "publication" / p["pub_id"] / "index.html"
        if not (page.is_file() and redirects_to(site, page, site / "publications.html")):
            bad.append(p["pub_id"])
    assert not bad, f"no redirect to publications.html for: {bad}"


def test_404_page_exists(site):
    assert (site / "404.html").is_file()


# --- 8. no demo content, no Google tags --------------------------------------


@pytest.mark.parametrize("folder", ["post", "courses", "slides", "event", "talk", "author", "admin"])
def test_no_template_folder(site, folder):
    files = [p for p in (site / folder).rglob("*") if p.is_file()] if (site / folder).exists() else []
    assert not files, f"_site/{folder}/ contains files"


@pytest.mark.parametrize("needle", [
    "Wowchemy", "Nelson Bighetti", "吳恩達", "G-NLVEJNN9LD", "googletagmanager", "google-analytics",
])
def test_no_page_contains(site, needle):
    hits = [p.relative_to(site).as_posix() for p in site.rglob("*.html")
            if needle in p.read_text(encoding="utf8", errors="replace")]
    assert not hits, f"{needle!r} found in {hits[:10]}"


@pytest.mark.parametrize("name", ["demo_resume.pdf", "googlef6990fc15f6a19f3.html"])
def test_no_unwanted_file(site, name):
    assert not list(site.rglob(name))


# --- 9. live site unchanged ----------------------------------------------------


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, encoding="utf8", errors="replace")


def test_change_only_adds_files(site):
    ref = _git("rev-parse", "--verify", "origin/master")
    assert ref.returncode == 0, "origin/master not available"
    diff = _git("diff", "--name-status", "origin/master")
    assert diff.returncode == 0, diff.stderr
    # Rendering the new index.qmd / 404.qmd replaces the old root Hugo files,
    # so exactly these two root deletions are allowed; any other D, M or R fails.
    allowed_deletions = {"index.html", "404.html"}
    not_added = []
    for line in diff.stdout.splitlines():
        if not line or line.startswith("A"):
            continue
        parts = line.split("\t")
        if parts[0] == "D" and len(parts) == 2 and parts[1] in allowed_deletions:
            continue
        not_added.append(line)
    assert not not_added, "changed/removed files vs origin/master:\n" + "\n".join(not_added[:30])


def test_build_output_not_tracked(site):
    out = _git("ls-files", "_site", ".quarto")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


# --- 10. workflow ------------------------------------------------------------


def _workflow():
    path = REPO_ROOT / ".github" / "workflows" / "publish.yml"
    assert path.is_file(), ".github/workflows/publish.yml missing"
    data = yaml.safe_load(path.read_text(encoding="utf8"))
    assert isinstance(data, dict)
    return data


def _steps(job):
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def test_workflow_triggers():
    wf = _workflow()
    on = wf.get("on", wf.get(True))  # PyYAML reads the key `on` as True
    assert isinstance(on, dict), "workflow `on:` must be a mapping"
    branches = (on.get("push") or {}).get("branches") or []
    assert "quarto" in branches and "master" in branches
    assert "pull_request" in on
    assert "workflow_dispatch" in on


def test_workflow_renders_and_uploads_site():
    jobs = _workflow().get("jobs") or {}
    for job in jobs.values():
        steps = _steps(job)
        renders = any(str(s.get("uses", "")).startswith("quarto-dev/quarto-actions/render")
                      or "quarto render" in str(s.get("run", "")) for s in steps)
        uploads = any(
            str(s.get("uses", "")).startswith("actions/upload-pages-artifact")
            # action default path is _site/
            and str((s.get("with") or {}).get("path", "_site")).strip().strip("./") in ("_site",)
            for s in steps)
        if renders and uploads:
            return
    pytest.fail("no job both renders with quarto and uploads _site with actions/upload-pages-artifact")


def test_workflow_deploy_gated_on_master_and_variable():
    jobs = _workflow().get("jobs") or {}
    deploy = [j for j in jobs.values()
              if any(str(s.get("uses", "")).startswith("actions/deploy-pages") for s in _steps(j))]
    assert deploy, "no job uses actions/deploy-pages"
    for job in deploy:
        cond = str(job.get("if", ""))
        assert re.search(r"github\.ref\s*==\s*'refs/heads/master'", cond), cond
        assert re.search(r"vars\.PAGES_DEPLOY\s*==\s*'true'", cond), cond
        assert "&&" in cond and "||" not in cond, cond


# --- 11. .gitignore ----------------------------------------------------------


def test_gitignore_ignores_build_output():
    path = REPO_ROOT / ".gitignore"
    assert path.is_file()
    lines = [line.strip() for line in path.read_text(encoding="utf8").splitlines()]
    assert "/_site/" in lines and "/.quarto/" in lines
