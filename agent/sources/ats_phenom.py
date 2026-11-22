"""Phenom career sites (e.g. Allianz: careers.allianz.com).

Robots-compliant: enumerate jobs from the public sitemap, keyword-pre-filter on the
URL slug (the title is in the slug), then read schema.org JSON-LD from the (allowed)
detail pages. Location isn't in the slug, so it's filtered from the JSON-LD after
fetching — hence a fetch cap.

`max_fetch` (registry override) bounds JSON-LD fetches per run. The default suits a
Germany-HQ site (relevant German roles cluster near the top of the sitemap); a GLOBAL
site may scatter its few local roles deep among hundreds of postings, so its
registry entry raises this to scan further down.
"""
from __future__ import annotations

import re

from agent.sources import base

_JOB_RE = re.compile(r"/job/(\d+)/")
_MAX_FETCH = 25


def search(query: base.JobQuery, *, company: str, base_url: str,
           max_fetch: int = _MAX_FETCH, **_) -> list:
    sitemap = base_url.rstrip("/") + "/sitemap.xml"
    cand = [u for u in base.sitemap_urls(sitemap)
            if "/job/" in u and base.url_maybe_relevant(u)]
    out = []
    for u in cand[:max_fetch]:
        ld = base.jsonld_jobposting(u)
        if not ld:
            continue
        m = _JOB_RE.search(u)
        ext = base.extraction_from_jsonld(
            ld, url=u, company=company, canonical_id=f"phenom:{m.group(1) if m else u}")
        if base.title_is_relevant(ext.role_guess) and base.location_in_scope(query, ext.location or ""):
            out.append(ext)
    return out
