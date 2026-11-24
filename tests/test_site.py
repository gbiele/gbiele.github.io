"""Tests for issue #1: the website rebuilt as a Quarto project (contract Part A).

Expected values come from the old Hugo output (captured in
tests/fixtures/legacy_site.json and the sentences hard-coded below) and from
the contract.
"""

import re
import subprocess
import unicodedata
from urllib.parse import urlparse

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
    "professor (II) in the Department of Psychology at the University of Oslo",
    "causal inference",
    "observational",
    "register",
    "parental income",
    "treatment effects",
    "confounding",
    "attrition",
    "missingness",
    "experimental psychology",
    "cognitive neuroscience",
    "Bayesian statistical modelling",
    "Causal inference",
    "Statistical modelling",
    "Bayesian statistics",
    "Cognitive modelling",
    "ADHD",
    "PhD, 2006",
    "Max Planck Institute for Human Development, Berlin",
    "Diploma (Psychology), 1999",
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
    "bsky.app/profile/guidobiele.bsky.social",
])
def test_about_page_has_contact_link(site, fragment):
    hrefs = [a["href"] for a in soup_of(site / "index.html").find_all("a", href=True)]
    assert any(fragment in h for h in hrefs), f"no link containing {fragment!r}"


# --- 3. research pages -------------------------------------------------------

OLD_TITLES = {
    "causalinf": "Causal inference",
    "bayes": "Bayesian statistics",
    "adhd": "ADHD",
    "epi": "Epidemiology",
    "decision-making": "Decision making & neuroscience",
}

# One plain-text sentence (no links/footnotes inside) per category page
# carrying legacy prose, plus a topic check for the new ADHD category page.
OLD_SENTENCES = {
    "causalinf": "The thematic focus of this research is on the effect of medication and special "
                 "education on educational and other outcomes of children with ADHD.",
    "bayes": "Statistical inference is at the core of scientific research because it allows "
             "us to learn in a systematic manner from data.",
    "adhd": "ADHD",
    "epi": "In comparison, inverse probability of participation weights will reduce bias.",
    "decision-making": "In hindsight it is not surprising that reciprocity explained behavior better.",
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
    nfrsea_target = (site / "research" / "nfrsea.html").resolve()
    seen = []
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if not href.strip() or href.startswith("#") or re.match(r"^[a-zA-Z][\w+.\-]*:", href):
            continue
        resolved = resolve(site, page, href).resolve()
        assert resolved != nfrsea_target, "nfrsea.html must not be linked as a top-level entry on research/index.html"
        slug = wanted.get(resolved)
        if slug and slug not in seen:
            seen.append(slug)
    assert set(seen) == set(RESEARCH_SLUGS)
    assert seen == ["causalinf", "bayes", "epi", "adhd", "decision-making"]


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


LEGACY_PROJECT_REDIRECTS = {
    "causalinf": "causalinf.html",
    "bayes": "bayes.html",
    "epi": "epi.html",
    "nfrsea": "nfrsea.html",
    "dm": "decision-making.html",
    "decneurosc": "decision-making.html",
    "cogneuro": "decision-making.html",
}


@pytest.mark.parametrize("slug,target", list(LEGACY_PROJECT_REDIRECTS.items()))
def test_old_project_url_redirects(site, slug, target):
    page = site / "project" / slug / "index.html"
    assert page.is_file()
    assert redirects_to(site, page, site / "research" / target)


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


# --- 12. issue #2 content audit ----------------------------------------------


def _normalize_quotes(text):
    return (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _is_twitter_or_x_host(href):
    host = (urlparse(href).hostname or "").lower()
    return (
        host in ("twitter.com", "x.com")
        or host.endswith(".twitter.com")
        or host.endswith(".x.com")
    )


def test_about_biography_covers_causal_inference_and_drops_sars_cov_2(site):
    page = site / "index.html"
    assert "sars-cov-2" not in page_text(page).casefold()
    bio_sec = soup_of(page).select_one("section#biography")
    assert bio_sec is not None, "missing #biography section on index.html"
    bio_lower = collapse_ws(bio_sec.get_text(" ")).casefold()
    for phrase in (
        "research professor in the department of child health and development "
        "at the norwegian institute of public health",
        "professor (ii) in the department of psychology at the university of oslo",
        "causal inference",
        "observational",
        "register",
        "adhd",
        "treatment effects",
        "parental income",
        "identification",
        "estimation",
        "confounding",
        "attrition",
        "missingness",
        "experimental psychology",
        "cognitive neuroscience",
        "bayesian statistical modelling",
    ):
        assert phrase in bio_lower, f"#biography missing {phrase!r}"


def test_about_interests_ordered_by_current_focus(site):
    sec = soup_of(site / "index.html").select_one("section#interests")
    assert sec is not None, "missing #interests section on index.html"
    items = [collapse_ws(li.get_text(" ")) for li in sec.select("ul > li")]
    assert items == [
        "Causal inference",
        "Bayesian statistics",
        "Statistical modelling",
        "ADHD",
        "Cognitive modelling",
    ]


def test_about_education_updated_years_and_drops_old_years(site):
    page = site / "index.html"
    text = page_text(page)
    assert "PhD, 2005" not in text
    assert "Diploma (Psychology), 1998" not in text
    sec = soup_of(page).select_one("section#education")
    assert sec is not None, "missing #education section on index.html"
    edu_text = collapse_ws(sec.get_text(" "))
    for expected in (
        "PhD, 2006",
        "Max Planck Institute for Human Development, Berlin",
        "Diploma (Psychology), 1999",
        "Free University Berlin",
    ):
        assert expected in edu_text, f"#education missing {expected!r}"


def test_about_contact_retains_address_and_redacts_email(site):
    page = site / "index.html"
    soup = soup_of(page)
    sec = soup.select_one("section#contact")
    assert sec is not None, "missing #contact section on index.html"
    contact_text = collapse_ws(sec.get_text(" "))
    assert "marcus thranes gate 6" in contact_text.casefold()
    assert "0473 Oslo" in contact_text
    assert "guido.biele@fhi.no" not in soup.get_text(" ").casefold()
    hrefs = [a["href"].casefold() for a in soup.find_all("a", href=True)]
    assert not any("guido.biele@fhi.no" in h or "mailto:guido.biele@fhi.no" in h for h in hrefs)


def test_about_social_links_replace_twitter_with_bluesky_and_no_orcid(site, legacy):
    page = site / "index.html"
    soup = soup_of(page)
    all_hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    assert not any(_is_twitter_or_x_host(h) for h in all_hrefs), "Twitter/X link found on index.html"
    assert not soup.select(".bi-twitter"), "Twitter icon .bi-twitter found on index.html"
    assert not any("orcid.org" in h.lower() for h in all_hrefs), "unexpected ORCID link on index.html"

    body_links = [
        a for a in (soup.body or soup).find_all("a", href=True)
        if a.find_parent("nav") is None
    ]
    body_hrefs = [a["href"] for a in body_links]
    for expected in (
        "https://bsky.app/profile/guidobiele.bsky.social",
        "https://scholar.google.com/citations?user=0UkWz9IAAAAJ&hl=en",
        "https://github.com/gbiele",
        "https://discourse.mc-stan.org/u/guido_biele/",
    ):
        assert expected in body_hrefs, f"about page body missing link {expected!r}"

    cv_target = (site / "files" / "CV_GuidoBiele.pdf").resolve()
    assert any(
        resolve(site, page, a["href"]).resolve() == cv_target
        and cv_target.is_file()
        and sha256(cv_target) != legacy["cv_sha256"]
        for a in body_links
    ), "about page body missing link to updated files/CV_GuidoBiele.pdf"


@pytest.mark.parametrize("rel_page", [
    "index.html",
    "research/index.html",
    "publications.html",
    "404.html",
])
def test_navbar_has_bluesky_github_scholar(site, rel_page):
    page = site / rel_page
    assert page.is_file()
    nav = soup_of(page).find("nav")
    assert nav is not None, f"missing <nav> on {rel_page}"
    links = nav.find_all("a", href=True)
    hrefs = [a["href"] for a in links]
    assert any("github.com/gbiele" in h for h in hrefs), f"navbar on {rel_page} missing GitHub"
    assert any("scholar.google.com/citations?user=0UkWz9IAAAAJ" in h for h in hrefs), (
        f"navbar on {rel_page} missing Google Scholar"
    )
    bsky_links = [
        a for a in links
        if a["href"] == "https://bsky.app/profile/guidobiele.bsky.social"
    ]
    assert bsky_links, f"navbar on {rel_page} missing Bluesky link"
    bsky = bsky_links[0]
    has_label = (
        bsky.get("aria-label") == "Bluesky"
        or bsky.find(attrs={"aria-label": "Bluesky"}) is not None
        or collapse_ws(bsky.get_text(" ")) == "Bluesky"
    )
    assert has_label, f"navbar Bluesky link on {rel_page} missing accessible label 'Bluesky'"
    assert bsky.select_one("i.bi-bluesky") is not None, (
        f"navbar Bluesky link on {rel_page} missing icon bi-bluesky"
    )


def test_research_index_descriptions_fixed(site):
    page = site / "research" / "index.html"
    soup = soup_of(page)
    descs = [
        collapse_ws(d.get_text(" "))
        for d in soup.select("section.research-interest p.research-interest-description")
    ]
    assert len(descs) == 5, f"expected 5 interest descriptions on research/index.html, got {len(descs)}"
    assert all(descs), f"empty interest description on research/index.html: {descs!r}"
    text = page_text(page)
    for bad in ("Epidemiologial", "special medication", "rsik factors", "sigfnificant", "reinfocement"):
        assert bad not in text, f"legacy typo {bad!r} found on research/index.html"


def test_research_epi_typos_and_alspac_https(site):
    page = site / "research" / "epi.html"
    soup = soup_of(page)
    text = page_text(page)
    meta_desc = soup.find("meta", attrs={"name": "description"})
    meta_content = meta_desc.get("content", "") if meta_desc else ""

    for bad in ("Epidemiologial", "rsik factors", "sigfnificant"):
        assert bad not in text, f"typo {bad!r} still present on research/epi.html"
        assert bad not in meta_content, f"typo {bad!r} still present in meta description"

    assert meta_content.strip(), "empty meta description on research/epi.html"
    assert "practically significant" in text

    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    assert "http://www.bristol.ac.uk/alspac/" not in hrefs
    assert "https://www.bristol.ac.uk/alspac/" in hrefs


def test_research_bayes_typos_and_publications_link(site):
    page = site / "research" / "bayes.html"
    soup = soup_of(page)
    text = _normalize_quotes(page_text(page))
    for bad in (
        "typ of research",
        "irrelvant",
        "the the ",
        'Click the "Bayesian" button below',
        "papers that uses Bayesian",
        "button below",
    ):
        assert bad not in text, f"stale/typo text {bad!r} still present on research/bayes.html"

    for expected in (
        "type of research",
        "practically irrelevant",
        "on the Stan user forum",
        "use Bayesian estimation",
    ):
        assert expected in text, f"missing expected text {expected!r} on research/bayes.html"

    main = soup.find("main") or soup.body or soup
    pub_target = (site / "publications.html").resolve()
    assert any(
        resolve(site, page, a["href"]).resolve() == pub_target and pub_target.is_file()
        for a in main.find_all("a", href=True)
    ), "research/bayes.html main content does not link to publications.html"


def test_research_causalinf_updated_opening_prose(site):
    page = site / "research" / "causalinf.html"
    text = page_text(page)
    assert "I am currently establishing this research line" not in text
    assert "being rewarded a grant" not in text

    soup = soup_of(page)
    main = soup.find("main") or soup.body or soup
    body_paras = collapse_ws(" ".join(p.get_text(" ") for p in main.find_all("p")))
    for expected in (
        "Norwegian Research Council",
        "grant",
        "register",
        "methodological",
        "causal inference",
        "The thematic focus of this research is on the effect of medication and special "
        "education on educational and other outcomes of children with ADHD.",
    ):
        assert expected in body_paras, f"missing {expected!r} in research/causalinf.html body"


def test_research_nfrsea_typos_dates_and_table(site):
    page = site / "research" / "nfrsea.html"
    soup = soup_of(page)
    text = page_text(page)
    meta_desc = soup.find("meta", attrs={"name": "description"})
    meta_content = meta_desc.get("content", "") if meta_desc else ""

    for bad in (
        "special medication",
        "2000 and 2100",
        "Statistics Norwy",
        "Karolinska Institiute",
        "Mats Fredrirksen",
        "This the begs",
    ):
        assert bad not in text, f"typo/stale text {bad!r} still present on research/nfrsea.html"
        assert bad not in meta_content

    expected_desc = (
        "Estimating effects of stimulant medication and special education "
        "on school performance using observational data"
    )
    assert expected_desc in meta_content
    for expected in (
        "born between 2000 and 2010",
        "Statistics Norway",
        "Karolinska Institute",
        "Mats Fredriksen",
        "This begs the question",
    ):
        assert expected in text, f"missing {expected!r} on research/nfrsea.html"

    table = soup.select_one("section#data table")
    assert table is not None, "missing data table on research/nfrsea.html"
    headers = [collapse_ws(th.get_text()) for th in table.select("thead tr th")]
    assert headers[-1] == "15 (t2)", f"unexpected final table header: {headers[-1]!r}"

    edu_rows = [
        tr for tr in table.select("tbody tr")
        if tr.find("td") and collapse_ws(tr.find("td").get_text()) == "Parental edu & income"
    ]
    assert len(edu_rows) == 1, "missing 'Parental edu & income' row"
    data_cells = [collapse_ws(td.get_text()) for td in edu_rows[0].find_all("td")[1:]]
    assert len(data_cells) == 7 and all(c == "SSB" for c in data_cells), (
        f"unexpected 'Parental edu & income' cells: {data_cells!r}"
    )


def test_research_nfrsea_collaborator_urls_updated(site):
    page = site / "research" / "nfrsea.html"
    soup = soup_of(page)
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    dead_urls = [
        "https://www.fhi.no/en/about/departments-and-centres/child-health-and-development/mari-vaage-wang/",
        "https://www.fhi.no/en/about/departments-and-centres/chronic-diseases-and-ageing/oystein-karlstad/",
        "https://samforsk.no/Sider/Ansatte/Christian-Wendelborg.aspx",
        "https://staff.ki.se/people/henlar",
        "https://www.northumbria.ac.uk/about-us/our-staff/c/reinie-cordier/",
    ]
    for url in dead_urls:
        assert url not in hrefs, f"dead 404 collaborator URL still present: {url}"

    expected_collaborators = {
        "Mari Vaage Wang": "https://www.uv.uio.no/iped/english/people/aca/marivw/",
        "Øystein Karlstad": "https://www.fhi.no/en/ab/departments-and-centres/chronic-diseases/oystein-karlstad/",
        "Christian Wendelborg": "https://samforsk.no/ansatte/christian-wendelborg",
        "Henrik Larsson": "https://ki.se/en/people/henrik-larsson",
        "Reinie Cordier": "https://profiles.curtin.edu.au/2482-reinie-cordier",
    }
    by_name = {collapse_ws(a.get_text()): a["href"] for a in soup.find_all("a", href=True)}
    for name, url in expected_collaborators.items():
        assert by_name.get(name) == url, f"expected {name} -> {url!r}, got {by_name.get(name)!r}"


def test_research_decision_making_typos_fixed(site):
    page = site / "research" / "decision-making.html"
    assert page.is_file()
    text = page_text(page)
    for bad in (
        "reinfocement",
        "Some og this work",
        "following reward can in itself",
        "to described",
    ):
        assert bad not in text, f"typo {bad!r} still present on research/decision-making.html"
    for expected in (
        "reinforcement learning",
        "following advice can in itself be rewarding",
        "Some of this work",
        "to describe the effect",
    ):
        assert expected in text, f"missing {expected!r} on research/decision-making.html"


def test_issue2_no_demo_taxonomy_or_media_files(site):
    for folder in ("category", "tag"):
        files = [p for p in (site / folder).rglob("*") if p.is_file()] if (site / folder).exists() else []
        assert not files, f"_site/{folder}/ contains files"
    for filename in ("boards.jpg", ".DS_Store"):
        assert not list(site.rglob(filename)), f"demo file {filename!r} found under _site"
    for needle in ("ReinforcmentLearning", "Hello R Markdown", "Welcome to Wowchemy"):
        hits = [
            p.relative_to(site).as_posix()
            for p in site.rglob("*.html")
            if needle in p.read_text(encoding="utf8", errors="replace")
        ]
        assert not hits, f"{needle!r} found in {hits[:10]}"
