"""Tiered job-description extraction from job-alert emails.

The agent's two real sources are LinkedIn (often JS/login-gated) and direct
company career pages / recruiter emails. Reliability varies wildly, so extraction
degrades gracefully and NEVER drops a job: a low-confidence result is still
returned (flagged) so you can open the link yourself.

apply.py re-derives company/role/industry authoritatively from the saved JD text,
so the *_guess fields here only need to be good enough for the digest line and the
dedup hash before the pipeline runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_STRUCTURE_KEYWORDS = [
    "responsibilities", "requirements", "qualifications", "what you", "your profile",
    "your role", "we offer", "about the role", "about you", "benefits",
    "aufgaben", "anforderungen", "qualifikation", "dein profil", "deine aufgaben",
    "wir bieten", "das bringst du", "deine aufgaben", "profil",
]

_LINKEDIN_JOB_RE = re.compile(r"/jobs(?:/view|/comm/jobs/view)?/(?:view/)?(\d{6,})", re.I)
_LINKEDIN_CURRENTID_RE = re.compile(r"currentJobId=(\d{6,})", re.I)

_SKIP_LINK_PATTERNS = re.compile(
    r"(unsubscribe|/comm/|/help|/settings|/psettings|notifications|/feed/|"
    r"facebook\.com|twitter\.com|x\.com|instagram\.com|youtube\.com|apple\.com|"
    r"play\.google|mailto:|/legal|/privacy|emailWebView|/account)", re.I)

MIN_JD_CHARS = 600          # a "substantial" JD body
MAX_JOBS_PER_EMAIL = 25     # guard against giant digests


@dataclass
class JDExtraction:
    text: str
    company_guess: str
    role_guess: str
    url: Optional[str]
    source: str               # 'linkedin' | 'career_page' | 'recruiter_email' | 'unknown'
    confidence: float         # 0.0–1.0
    notes: str = ""
    canonical_id: Optional[str] = None   # e.g. 'linkedin:3912345678' — for dedup hashing
    location: Optional[str] = None


# ── HTTP ────────────────────────────────────────────────────────────────────────
def _http_get(url: str, timeout: int = 10) -> Optional[str]:
    import requests
    for _ in range(2):
        try:
            r = requests.get(url, timeout=timeout,
                             headers={"User-Agent": _UA,
                                      "Accept-Language": "en,de;q=0.8"})
            if r.status_code == 200 and r.text:
                return r.text
        except requests.RequestException:
            pass
    return None


# ── text helpers ────────────────────────────────────────────────────────────────
def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _structure_score(text: str) -> int:
    low = text.lower()
    return sum(1 for kw in _STRUCTURE_KEYWORDS if kw in low)


def _confidence(text: str, base: float) -> float:
    """Combine a source-tier base with length + structure signals."""
    n = len(text)
    if n < 250:
        return min(base, 0.3)
    bonus = 0.0
    if n >= MIN_JD_CHARS:
        bonus += 0.1
    if n >= 1500:
        bonus += 0.05
    if _structure_score(text) >= 2:
        bonus += 0.1
    return round(min(0.95, base + bonus), 2)


def _main_content(html: str) -> str:
    """Pull the main JD text from a fetched page: strip chrome, prefer
    <main>/[role=main]/<article>, else the largest text block."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form",
                     "noscript", "svg"]):
        tag.decompose()
    candidates = []
    for sel in ["main", "[role=main]", "article",
                ".description__text", ".show-more-less-html__markup",
                ".jobs-description", "#job-details", ".job-description"]:
        for el in soup.select(sel):
            candidates.append(el.get_text("\n"))
    if not candidates:
        # largest block-level element by text length
        blocks = soup.find_all(["div", "section", "td"])
        candidates = [b.get_text("\n") for b in blocks]
    best = max(candidates, key=lambda t: len(t.strip()), default="")
    return _clean(best)


# ── subject / title guessing ────────────────────────────────────────────────────
def _guess_from_subject(subject: str) -> tuple[str, str]:
    """Best-effort (role, company) from common alert subject shapes."""
    s = re.sub(r"^(re|fwd?):\s*", "", subject, flags=re.I).strip()
    # "<Role> at <Company>"  /  "<Role> bei <Company>"
    m = re.search(r"(.+?)\s+(?:at|bei|@)\s+(.+)", s, re.I)
    if m:
        return m.group(1).strip(" -–—:"), m.group(2).strip(" -–—:")
    # "<Company> is hiring: <Role>"  /  "<Company> sucht: <Role>"
    m = re.search(r"(.+?)\s+(?:is hiring|sucht|hiring)\s*:?\s*(.+)", s, re.I)
    if m:
        return m.group(2).strip(" -–—:"), m.group(1).strip(" -–—:")
    # "Your job alert for <Role>"
    m = re.search(r"job alert(?:s)?\s+(?:for|:)\s+(.+)", s, re.I)
    if m:
        return m.group(1).strip(" -–—:"), ""
    return s, ""


# ── LinkedIn ────────────────────────────────────────────────────────────────────
def _linkedin_job_ids(links: list[str]) -> list[tuple[str, str]]:
    """Return [(job_id, url)] for distinct LinkedIn job postings in the links."""
    found: dict[str, str] = {}
    for url in links:
        m = _LINKEDIN_JOB_RE.search(url) or _LINKEDIN_CURRENTID_RE.search(url)
        if m and m.group(1) not in found:
            found[m.group(1)] = url
    return list(found.items())


def _nearest_block_text(soup: BeautifulSoup, job_id: str) -> tuple[str, str]:
    """Find the <a> for this job and return (role_text, surrounding_block_text)."""
    anchor = None
    for a in soup.find_all("a", href=True):
        if job_id in a["href"]:
            anchor = a
            break
    if anchor is None:
        return "", ""
    role = _clean(anchor.get_text(" "))
    block = anchor
    for _ in range(4):                       # climb to a card-ish ancestor
        if block.parent is None:
            break
        block = block.parent
        if block.name in ("td", "table", "li", "article", "section"):
            break
    return role, _clean(block.get_text("\n")) if block else ""


_AUTHWALL_RE = re.compile(
    r"(sign in to|join linkedin|authwall|sign up to view|register to see|"
    r"join now to see|melde dich an|jetzt anmelden|no longer (?:accepting|available)|"
    r"this job is no longer|page not found|couldn.t find|could not find)", re.I)


def _looks_like_authwall(text: str) -> bool:
    return bool(_AUTHWALL_RE.search(text[:4000]))


def _linkedin_guest_jd(job_id: str) -> Optional[str]:
    """Best-effort guest fetch of the full JD via LinkedIn's guest job-posting
    endpoint. Returns None on the common login-wall / blocked responses so a wall
    is never mistaken for a real description (only the specific description
    containers are accepted — no greedy main-content fallback here)."""
    html = _http_get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    if not html or _looks_like_authwall(html):
        return None
    soup = BeautifulSoup(html, "lxml")
    for sel in (".show-more-less-html__markup", ".description__text",
                "section.description", "div.description"):
        el = soup.select_one(sel)
        if el:
            txt = _clean(el.get_text("\n"))
            if len(txt) > 300 and not _looks_like_authwall(txt):
                return txt
    return None


def _extract_linkedin(msg, enrich: bool) -> list[JDExtraction]:
    soup = BeautifulSoup(msg.html or "", "lxml") if msg.html else None
    job_ids = _linkedin_job_ids(msg.links)
    if not job_ids:
        return []                       # a LinkedIn notification email with no job cards

    out: list[JDExtraction] = []
    sub_role, sub_company = _guess_from_subject(msg.subject)
    for job_id, _ in job_ids[:MAX_JOBS_PER_EMAIL]:
        block = _nearest_block_text(soup, job_id)[1] if soup is not None else ""
        role, company, location = _parse_linkedin_card(block)
        guest = _linkedin_guest_jd(job_id) if enrich else None     # Tier 2 (shortlist only)
        if guest and len(guest) >= MIN_JD_CHARS:                    # full JD fetched
            role_guess, company_guess = (role or sub_role), (company or sub_company)
            text, conf, notes = guest, _confidence(guest, 0.85), ""
        elif role and company:                                     # a real alert card
            role_guess, company_guess = role, company
            text, conf, notes = block, 0.6, "Title from alert; full posting fetched at shortlist"
        else:
            continue   # an extra 'see more'/recommendation id with no real card — skip

        out.append(JDExtraction(
            text=_compose_jd(role_guess, company_guess, text),
            company_guess=company_guess, role_guess=role_guess, location=location or None,
            url=f"https://www.linkedin.com/jobs/view/{job_id}",
            source="linkedin", confidence=conf, notes=notes,
            canonical_id=f"linkedin:{job_id}"))
    if out:
        return out

    # No cards parsed, but the subject names a specific role → single-job alert.
    # Attach the primary job id so it dedupes against any card version elsewhere.
    if sub_role and sub_company:
        job_id = job_ids[0][0]
        guest = _linkedin_guest_jd(job_id) if enrich else None
        if guest and len(guest) >= MIN_JD_CHARS:
            text, conf, notes = guest, _confidence(guest, 0.85), ""
        else:
            text, conf = (msg.body[:1500] or msg.subject), 0.55
            notes = "Title from alert subject; full posting fetched at shortlist"
        return [JDExtraction(
            text=_compose_jd(sub_role, sub_company, text),
            company_guess=sub_company, role_guess=sub_role,
            url=f"https://www.linkedin.com/jobs/view/{job_id}",
            source="linkedin", confidence=conf, notes=notes,
            canonical_id=f"linkedin:{job_id}")]
    return []   # summary/notification LinkedIn email with no specific job


def _parse_linkedin_card(block: str) -> tuple[str, str, str]:
    """LinkedIn alert cards render as 'Role title' then 'Company · Location'.
    Returns (role, company, location)."""
    lines = [ln.strip() for ln in (block or "").splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        parts = re.split(r"\s*[·•∙]\s*", line, maxsplit=1)
        if len(parts) == 2:
            role = " ".join(lines[:i]).strip()
            return role, parts[0].strip(), parts[1].strip()
    return (lines[0] if lines else ""), "", ""


# ── generic (career page / recruiter email) ─────────────────────────────────────
def _first_content_link(links: list[str]) -> Optional[str]:
    for url in links:
        if not _SKIP_LINK_PATTERNS.search(url):
            return url
    return None


def _extract_generic(msg, force_source: Optional[str] = None,
                     enrich: bool = True) -> JDExtraction:
    role_guess, company_guess = _guess_from_subject(msg.subject)
    body = msg.body.strip()

    # (1) the email itself is the JD (recruiter pasted it inline) — no fetch needed
    if len(body) >= MIN_JD_CHARS and _structure_score(body) >= 2:
        return JDExtraction(
            text=_compose_jd(role_guess, company_guess, body),
            company_guess=company_guess, role_guess=role_guess, url=None,
            source=force_source or "recruiter_email",
            confidence=_confidence(body, 0.7), notes="JD taken from email body")

    url = _first_content_link(msg.links)

    # (2) fetch the first real link — only when enriching (skipped during bulk ingest)
    if enrich and url:
        html = _http_get(url)
        if html:
            page = _main_content(html)
            if not company_guess:
                company_guess = _og_site_name(html)
            if len(page) >= MIN_JD_CHARS:
                return JDExtraction(
                    text=_compose_jd(role_guess, company_guess, page),
                    company_guess=company_guess, role_guess=role_guess, url=url,
                    source=force_source or "career_page",
                    confidence=_confidence(page, 0.7), notes="")

    # (3) low-confidence fallback: the email body/subject (full posting fetched
    #     later by enrich() if this job gets shortlisted for the pipeline)
    text = body or msg.subject
    return JDExtraction(
        text=_compose_jd(role_guess, company_guess, text),
        company_guess=company_guess, role_guess=role_guess, url=url,
        source=force_source or ("career_page" if url else "unknown"),
        confidence=_confidence(text, 0.3),
        notes="JD extraction incomplete — full posting fetched if shortlisted")


def _og_site_name(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for prop in ("og:site_name", "og:title"):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return tag["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:80]
    return ""


# ── composition ─────────────────────────────────────────────────────────────────
def _compose_jd(role: str, company: str, body: str) -> str:
    """Prepend a header line so apply.py's analyze_jd reliably sees company/role
    even when the scraped body buries them."""
    header = []
    if role:
        header.append(f"Role: {role}")
    if company:
        header.append(f"Company: {company}")
    head = ("\n".join(header) + "\n\n") if header else ""
    return (head + body).strip()


# ── dispatch ────────────────────────────────────────────────────────────────────
def _is_linkedin(msg) -> bool:
    return ("linkedin" in (msg.sender or "").lower()
            or any("linkedin.com" in lnk for lnk in msg.links))


def extract_jobs(msg, enrich: bool = True) -> list[JDExtraction]:
    """Top-level: turn one job-alert email into one or more JDExtractions.

    Pass enrich=False during bulk ingest to skip all web fetches (rank on the email
    snippet only); call enrich() later for the few jobs that get shortlisted."""
    if _is_linkedin(msg):
        return _extract_linkedin(msg, enrich)
    return [_extract_generic(msg, enrich=enrich)]


def enrich(source: str, jd_url: Optional[str]) -> Optional[str]:
    """Fetch the FULL job description for a shortlisted job. Called at pipeline time
    only, so web scraping is bounded to the handful of jobs actually pipelined
    (not the whole nightly backlog). Returns fuller JD text, or None."""
    if not jd_url:
        return None
    if source == "linkedin":
        m = re.search(r"/jobs/view/(\d+)", jd_url)
        return _linkedin_guest_jd(m.group(1)) if m else None
    if source in ("career_page", "career_site", "aggregator", "adzuna", "arbeitnow"):
        html = _http_get(jd_url)
        if html:
            page = _main_content(html)
            return page if len(page) >= MIN_JD_CHARS else None
    return None
