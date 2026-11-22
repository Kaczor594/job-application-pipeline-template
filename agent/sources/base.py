"""Shared helpers for source connectors: the search query, normalization to a
JDExtraction, an exclusion filter, and a small JSON GET."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from agent import config
from agent.jd_extract import JDExtraction, _UA, _clean, _compose_jd

from agent import market as _market


def title_is_relevant(title: str) -> bool:
    """Coarse title pre-filter from job_search (domain word + role word). It only
    de-noises broad feeds; the LLM ranker remains the real relevance gate."""
    return _market.load().title_is_relevant(title)


@dataclass
class JobQuery:
    terms: list[str]
    location: str = ""
    country_code: str = ""
    remote: str = "country"
    excluded_companies: list[str] = field(default_factory=list)

    @property
    def remote_ok(self) -> bool:
        return self.remote != "none"

    @classmethod
    def from_profile(cls) -> "JobQuery":
        """Search terms + location from profile.json → job_search (or the
        JOBAGENT_DISCOVER_TERMS override)."""
        m = _market.load()
        return cls(terms=m.search_terms or ["Data Analyst"], location=m.city,
                   country_code=m.country_code, remote=m.remote,
                   excluded_companies=m.excluded_companies)


def is_excluded_company(query: JobQuery, company: str) -> bool:
    c = (company or "").lower()
    return any(x in c for x in query.excluded_companies)


def location_in_scope(query: JobQuery, location: str) -> bool:
    """Your city, plus remote roles when job_search.location.remote allows them."""
    return _market.load().location_in_scope(location)


def normalize_posting(*, title: str, company: str, location: Optional[str],
                      description: str, url: Optional[str], source: str,
                      canonical_id: Optional[str], confidence: float = 0.75,
                      salary_note: str = "") -> JDExtraction:
    """Turn a source posting into the canonical JDExtraction shape."""
    title = (title or "").strip() or "Unknown role"
    company = (company or "").strip() or "Unknown"
    body = _clean(description or "")
    if salary_note:
        body = f"{body}\n\n{salary_note}".strip()
    text = _compose_jd(title, company, body or title)
    # No description ⇒ snippet-only; flag lower confidence (enrich() fills it at pipeline time).
    conf = confidence if len(body) >= 200 else min(confidence, 0.45)
    return JDExtraction(
        text=text, company_guess=company, role_guess=title, url=url, source=source,
        confidence=conf, notes=f"discovered via {source}", canonical_id=canonical_id,
        location=(location or None))


def http_get_json(url: str, params: Optional[dict] = None, timeout: int = 12):
    """GET JSON. Returns the decoded body (dict OR list — Lever returns a list), or None."""
    import requests
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": _UA, "Accept": "application/json"})
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def http_post_json(url: str, payload: dict, timeout: int = 12):
    """POST JSON, return decoded body or None (Workday cxs search endpoint)."""
    import requests
    try:
        r = requests.post(url, json=payload, timeout=timeout,
                          headers={"User-Agent": _UA, "Accept": "application/json"})
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def http_get_text(url: str, params: Optional[dict] = None, timeout: int = 12) -> Optional[str]:
    """GET raw text (for XML feeds, e.g. Personio)."""
    import requests
    try:
        r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": _UA})
        if r.status_code == 200 and r.text:
            return r.text
    except requests.RequestException:
        pass
    return None


def html_to_text(s: str) -> str:
    """Unescape entities and strip tags from an ATS description blob (e.g. Greenhouse)."""
    import html as _html
    return _clean(re.sub(r"<[^>]+>", " ", _html.unescape(s or "")))


# ── sitemap + JSON-LD (Phenom/Radancy career sites) ──────────────────────────────
def url_maybe_relevant(url: str) -> bool:
    """A job URL slug worth fetching: mentions one of your domain/role words."""
    return _market.load().url_maybe_relevant(url)


def sitemap_urls(url: str, *, max_subsitemaps: int = 6) -> list[str]:
    """Return all <loc> URLs from a sitemap, transparently descending one level into
    a <sitemapindex> (bounded). Used to enumerate job pages without hitting a
    robots-disallowed search API."""
    txt = http_get_text(url)
    if not txt:
        return []
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", txt)
    if "<sitemapindex" in txt:
        out: list[str] = []
        for sub in locs[:max_subsitemaps]:
            sub_txt = http_get_text(sub)
            if sub_txt:
                out += re.findall(r"<loc>\s*(.*?)\s*</loc>", sub_txt)
        return out
    return locs


def jsonld_jobposting(url: str) -> Optional[dict]:
    """Fetch a job detail page and return its schema.org JobPosting JSON-LD, or None."""
    html = http_get_text(url)
    if not html:
        return None
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except (ValueError, TypeError):
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
                return obj
    return None


def _ld_location(ld: dict) -> str:
    parts: list[str] = []
    locs = ld.get("jobLocation")
    for loc in (locs if isinstance(locs, list) else [locs]):
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address") or {}
        for k in ("addressLocality", "addressRegion", "addressCountry"):
            v = addr.get(k)
            if v and v not in parts:
                parts.append(v)
    if ld.get("jobLocationType") == "TELECOMMUTE" or ld.get("applicantLocationRequirements"):
        parts.append("Remote")
    return ", ".join(parts)


def extraction_from_jsonld(ld: dict, *, url: str, company: str,
                           canonical_id: str) -> JDExtraction:
    # Use the curated registry `company`, NOT the JSON-LD hiringOrganization: the
    # latter is often a parent/group brand (e.g. "Brand | Group | Holding") that
    # breaks de-dupe against the same employer seen under its plain name.
    return normalize_posting(
        title=ld.get("title") or "", company=company,
        location=_ld_location(ld), description=html_to_text(ld.get("description") or ""),
        url=url, source="career_site", canonical_id=canonical_id, confidence=0.8)
