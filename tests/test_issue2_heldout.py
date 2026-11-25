"""Held-out checks for issue #2 (same contract, independent angles/contexts)."""

from urllib.parse import urlparse

from site_helpers import collapse_ws, resolve, soup_of


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


def test_heldout_sitewide_email_redacted_and_no_twitter_hosts(site):
    html_pages = sorted(site.rglob("*.html"))
    assert html_pages
    for page in html_pages:
        raw = page.read_text(encoding="utf8", errors="replace")
        rel = page.relative_to(site).as_posix()
        assert "guido.biele@fhi.no" not in raw.casefold(), f"email leaked in {rel}"
        soup = soup_of(page)
        assert not soup.select(".bi-twitter"), f"Twitter icon .bi-twitter leaked in {rel}"
        for a in soup.find_all("a", href=True):
            href = a["href"]
            assert "mailto:guido.biele@fhi.no" not in href.casefold(), f"mailto leaked in {rel}"
            assert not _is_twitter_or_x_host(href), f"Twitter/X host {href!r} found in {rel}"

    for rel in ("research/epi.html", "research/bayes.html", "publications.html", "404.html"):
        nav = soup_of(site / rel).find("nav")
        assert nav is not None, f"missing <nav> on {rel}"
        nav_links = nav.find_all("a", href=True)
        nav_hrefs = [a["href"] for a in nav_links]
        assert not any("orcid.org" in h.lower() for h in nav_hrefs), f"unexpected ORCID in nav on {rel}"
        assert any("github.com/gbiele" in h for h in nav_hrefs), f"missing GitHub in nav on {rel}"
        assert any("scholar.google.com/citations?user=0UkWz9IAAAAJ" in h for h in nav_hrefs), (
            f"missing Google Scholar in nav on {rel}"
        )
        bsky = [a for a in nav_links if a["href"] == "https://bsky.app/profile/guidobiele.bsky.social"]
        assert bsky, f"missing Bluesky link in nav on {rel}"
        assert bsky[0].select_one("i.bi-bluesky") is not None, f"missing bi-bluesky icon on {rel}"
        has_label = (
            bsky[0].get("aria-label") == "Bluesky"
            or bsky[0].find(attrs={"aria-label": "Bluesky"}) is not None
            or collapse_ws(bsky[0].get_text(" ")) == "Bluesky"
        )
        assert has_label, f"missing accessible Bluesky label on {rel}"


def test_heldout_research_meta_descriptions_and_listing_cards(site):
    epi_soup = soup_of(site / "research" / "epi.html")
    epi_meta = epi_soup.find("meta", attrs={"name": "description"})
    assert epi_meta is not None
    epi_desc = epi_meta.get("content", "").strip()
    assert epi_desc
    assert "Epidemiologial" not in epi_desc
    assert "rsik factors" not in epi_desc

    nfrsea_soup = soup_of(site / "research" / "nfrsea.html")
    nfrsea_meta = nfrsea_soup.find("meta", attrs={"name": "description"})
    assert nfrsea_meta is not None
    nfrsea_desc = nfrsea_meta.get("content", "")
    assert "special medication" not in nfrsea_desc
    assert (
        "Estimating effects of stimulant medication and special education "
        "on school performance using observational data"
    ) == nfrsea_desc

    idx_soup = soup_of(site / "research" / "index.html")
    cards = idx_soup.select(".research-cards-grid article.research-card")
    assert len(cards) == 5
    card_titles = [
        collapse_ws(h.get_text(" "))
        for c in cards
        if (h := c.select_one("h2.research-card-title")) is not None
    ]
    assert len(card_titles) == 5 and all(card_titles), card_titles

    descs = []
    for slug in ("causalinf", "bayes", "epi", "adhd", "decision-making"):
        topic_soup = soup_of(site / "research" / f"{slug}.html")
        topic_descs = [
            collapse_ws(d.get_text(" "))
            for d in topic_soup.select("section.research-interest p.research-interest-description")
        ]
        assert len(topic_descs) == 1 and topic_descs[0], (slug, topic_descs)
        descs.extend(topic_descs)
    assert len(descs) == 5
    assert not any("Epidemiologial" in c or "special medication" in c for c in descs), descs


def test_heldout_nfrsea_full_table_structure_and_collaborator_section(site):
    page = site / "research" / "nfrsea.html"
    soup = soup_of(page)

    data_sec = soup.select_one("section#data")
    assert data_sec is not None
    data_text = collapse_ws(data_sec.get_text(" "))
    assert "2000 and 2100" not in data_text
    assert "born between 2000 and 2010" in data_text

    table = data_sec.find("table")
    assert table is not None
    headers = [collapse_ws(th.get_text()) for th in table.select("thead tr th")]
    assert headers == ["Age", "0", "5", "6,7", "8", "10 (t1)", "11-14", "15 (t2)"]

    rows = [
        [collapse_ws(td.get_text()) for td in tr.find_all("td")]
        for tr in table.select("tbody tr")
    ]
    edu_rows = [r for r in rows if r and r[0] == "Parental edu & income"]
    assert len(edu_rows) == 1
    assert edu_rows[0] == ["Parental edu & income"] + ["SSB"] * 7

    raw = page.read_text(encoding="utf8", errors="replace")
    for dead_url in (
        "https://www.fhi.no/en/about/departments-and-centres/child-health-and-development/mari-vaage-wang/",
        "https://www.fhi.no/en/about/departments-and-centres/chronic-diseases-and-ageing/oystein-karlstad/",
        "https://samforsk.no/Sider/Ansatte/Christian-Wendelborg.aspx",
        "https://staff.ki.se/people/henlar",
        "https://www.northumbria.ac.uk/about-us/our-staff/c/reinie-cordier/",
    ):
        assert dead_url not in raw, f"dead URL {dead_url!r} still present in nfrsea.html"

    collab_sec = soup.select_one("section#collaborators")
    assert collab_sec is not None
    by_name = {collapse_ws(a.get_text()): a["href"] for a in collab_sec.find_all("a", href=True)}
    for name, url in {
        "Jon Michael Gran": "https://www.med.uio.no/imb/english/people/aca/jonmic/",
        "Mari Vaage Wang": "https://www.uv.uio.no/iped/english/people/aca/marivw/",
        "Øystein Karlstad": "https://www.fhi.no/en/ab/departments-and-centres/chronic-diseases/oystein-karlstad/",
        "Christian Wendelborg": "https://samforsk.no/ansatte/christian-wendelborg",
        "Henrik Larsson": "https://ki.se/en/people/henrik-larsson",
        "Reinie Cordier": "https://profiles.curtin.edu.au/2482-reinie-cordier",
    }.items():
        assert by_name.get(name) == url


def test_heldout_bayes_quote_normalized_dom_and_publications_target(site):
    page = site / "research" / "bayes.html"
    soup = soup_of(page)
    main = soup.find("main") or soup.body or soup
    para_text = _normalize_quotes(collapse_ws(" ".join(p.get_text(" ") for p in main.find_all("p"))))

    for bad in (
        'Click the "Bayesian" button below',
        "the the ",
        "typ of research",
        "irrelvant",
        "papers that uses Bayesian",
        "button below",
    ):
        assert bad not in para_text, f"stale/typo text {bad!r} found in bayes.html <main> paragraphs"

    for good in (
        "type of research",
        "practically irrelevant",
        "on the Stan user forum",
        "use Bayesian estimation",
    ):
        assert good in para_text, f"missing {good!r} in bayes.html <main> paragraphs"

    pub_page = (site / "publications.html").resolve()
    pub_links = [
        a for a in main.find_all("a", href=True)
        if resolve(site, page, a["href"]).resolve() == pub_page
    ]
    assert pub_links, "bayes.html main content does not link to publications.html"
    assert pub_page.is_file()

    entries = soup_of(pub_page).select("article.pub-entry")
    assert len(entries) == 69
    assert all(len(e.select("strong.pub-author-self")) == 1 for e in entries)
