"""my-job-shop.com career sites (Typesense backend) — e.g. EY (jobsgermany.ey.com).

These are Nuxt SPAs over a shared my-job-shop Typesense index. We replicate the site's
own public search: fetch a short-lived, search-only Typesense key scoped to the
employer's job-shop vanities, then POST a single Typesense `multi_search` carrying all
term-searches at once (1 round-trip, not one per term). The tenant / jobshop /
vanity identifiers are stable, so they live in the registry entry (like Workday's
host/tenant/site). The Typesense document already carries the JD body, so no enrich()
round-trip is needed.

Registry entry:
    {"company": "EY", "platform": "myjobshop", "base_url": "https://api.my-job-shop.com",
     "tenant_id": "ernst-young", "jobshop_id": "<uuid>",
     "vanities": ["ey-absolventen", "ey-studentin", ...]}
"""
from __future__ import annotations

import requests

from agent.jd_extract import _UA
from agent.sources import base

_KEY_PATH = "/api/offer/v1/search/api-key"
_SEARCH_PATH = "/api/typesense/multi_search"
_QUERY_BY = "title, location, company, full_address"
_BODY_FIELDS = ("introduction", "about", "expectation", "offering", "description")
_PER_PAGE = 50


def _join(v) -> str:
    return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v or "")


def _jd_body(doc: dict) -> str:
    parts = [base.html_to_text(_join(doc.get(f))) for f in _BODY_FIELDS if doc.get(f)]
    return "\n\n".join(p for p in parts if p).strip()


def search(query: base.JobQuery, *, company: str, base_url: str, tenant_id: str,
           jobshop_id: str, vanities: list, **_) -> list:
    api = base_url.rstrip("/")
    hdr = {"User-Agent": _UA, "Accept": "application/json"}
    filt = ",".join(f"backoffice_vanity:{v}" for v in vanities)
    try:                                        # short-lived, search-only scoped key
        kr = requests.get(f"{api}{_KEY_PATH}", params={"filter": filt},
                          headers={**hdr, "X-Tenant-Id": tenant_id}, timeout=12)
        key = kr.json().get("key") if kr.status_code == 200 else None
    except (requests.RequestException, ValueError):
        key = None
    if not key:
        return []
    sh = {**hdr, "Content-Type": "application/json", "X-Tenant-Id": tenant_id,
          "X-JobShop-Id": jobshop_id, "X-Typesense-Api-Key": key}
    # All term-searches go in ONE multi_search POST — Typesense returns a parallel
    # `results[]` array (one block per search), so 6 terms = 1 round-trip, not 6.
    searches = [{"collection": "offers", "q": term, "query_by": _QUERY_BY,
                 "per_page": _PER_PAGE, "page": 1} for term in query.terms[:6]]
    if not searches:
        return []
    try:
        res = requests.post(f"{api}{_SEARCH_PATH}", json={"searches": searches},
                            headers=sh, timeout=12).json()
        blocks = res.get("results", [])
    except (requests.RequestException, ValueError):
        return []
    seen: set = set()
    out: list = []
    for block in blocks:
        for hit in block.get("hits", []):
            doc = hit.get("document", {})
            jid = str(doc.get("offer_uuid") or doc.get("external_id") or doc.get("id") or "")
            title = doc.get("title") or ""
            loc = _join(doc.get("location"))
            if not jid or jid in seen:
                continue
            if not base.title_is_relevant(title) or not base.location_in_scope(query, loc):
                continue
            seen.add(jid)
            out.append(base.normalize_posting(
                title=title, company=company, location=loc, description=_jd_body(doc),
                url=doc.get("url"), source="career_site",
                canonical_id=f"myjobshop:{jid}", confidence=0.75))
    return out
