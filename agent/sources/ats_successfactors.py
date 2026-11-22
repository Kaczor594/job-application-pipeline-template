"""SAP SuccessFactors Career Site Builder (CSB) — server-rendered job search.

Reads the public, robots-ALLOWED /search/ results page (the richer /services/rss
feed is robots-disallowed on these sites, so we don't touch it). Each job tile in the
SSR table carries title + location + id but no JD body, so — like the Workday /
SmartRecruiters connectors — enrich() fetches the full JD from the public job URL at
pipeline time. CSB filters by location server-side (?location=…), so we query the
profile's city directly instead of pulling the whole board.

Registry entry:
    {"company": "...", "platform": "successfactors", "base_url": "https://careers.host.com"}
Optional: "location" (defaults to the query's city), "max_pages".
"""
from __future__ import annotations

import html as _html
import re

from agent.sources import base

_ROW_RE = re.compile(r'<tr class="data-row">(.*?)</tr>', re.S)
# one tile: href (…/{id}/) + the visible title
_LINK_RE = re.compile(r'<a href="(/job/[^"]+?/(\d+)/?)"[^>]*class="jobTitle-link">(.*?)</a>', re.S)
_LOC_RE = re.compile(r'class="colLocation[^"]*"[^>]*>(.*?)</td>', re.S)
_PAGE = 25


def _text(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def _city(loc: str | None) -> str:
    """Reduce a profile location ('Berlin area', 'Berlin, Germany') to the bare city
    CSB's ?location= facet expects ('Berlin'). Empty = no location filter."""
    city = (loc or "").split(",")[0].strip()
    for suf in (" metropolitan area", " metropolitan", " area", " region"):
        if city.lower().endswith(suf):
            city = city[: -len(suf)].strip()
    return city


def search(query: base.JobQuery, *, company: str, base_url: str,
           location: str | None = None, max_pages: int = 4, **_) -> list:
    host = base_url.rstrip("/")
    loc_q = location or _city(query.location)
    seen: set = set()
    out: list = []
    for start in range(0, max_pages * _PAGE, _PAGE):
        h = base.http_get_text(f"{host}/search/", {"q": "", "location": loc_q, "startrow": start})
        if not h:
            break
        rows = _ROW_RE.findall(h)
        if not rows:
            break
        for row in rows:
            lm = _LINK_RE.search(row)
            if not lm:
                continue
            path, jid, title = _html.unescape(lm.group(1)), lm.group(2), _text(lm.group(3))
            if jid in seen:
                continue
            loc_m = _LOC_RE.search(row)
            loctext = _text(loc_m.group(1)) if loc_m else loc_q
            if not base.title_is_relevant(title) or not base.location_in_scope(query, loctext):
                continue
            seen.add(jid)
            out.append(base.normalize_posting(
                title=title, company=company, location=loctext, description="",
                url=f"{host}{path}", source="career_site",
                canonical_id=f"successfactors:{jid}", confidence=0.55))
        if len(rows) < _PAGE:
            break
    return out
