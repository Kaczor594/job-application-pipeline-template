"""Radancy career sites (many large corporates use them; e.g. careers.<company>.com).

Robots-compliant: Radancy disallows /search-jobs/ for generic bots, but the sitemap
is allowed and its job URLs carry BOTH location and title in the slug
(/job/{location}/{title-slug}/{cat}/{id}). So we pre-filter to your location + relevant
roles on the URL alone, then read JSON-LD only from that handful of detail pages.
"""
from __future__ import annotations

import re

from agent.sources import base

_JOB_RE = re.compile(r"/job/([^/]+)/([^/]+)/\d+/(\d+)")
_MAX_FETCH = 20


def search(query: base.JobQuery, *, company: str, base_url: str, **_) -> list:
    sitemap = base_url.rstrip("/") + "/sitemap.xml"
    cand = []
    for u in base.sitemap_urls(sitemap):
        m = _JOB_RE.search(u)
        if not m:
            continue
        loc_slug, title_slug, jid = m.group(1), m.group(2), m.group(3)
        if not base.url_maybe_relevant(title_slug):
            continue
        if not base.location_in_scope(query, loc_slug.replace("-", " ")):
            continue
        cand.append((u, jid))

    out = []
    for u, jid in cand[:_MAX_FETCH]:
        ld = base.jsonld_jobposting(u)
        if not ld:
            continue
        ext = base.extraction_from_jsonld(ld, url=u, company=company,
                                          canonical_id=f"radancy:{jid}")
        # Trust the URL slug for location (the JSON-LD sometimes omits it).
        if base.title_is_relevant(ext.role_guess):
            out.append(ext)
    return out
