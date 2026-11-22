"""Greenhouse — public boards API (clean JSON, full JD via content=true).

GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
Used by many scaleups. `slug` is the board token (e.g. 'stripe').
"""
from __future__ import annotations

from agent.sources import base

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def search(query: base.JobQuery, *, slug: str, company: str, **_) -> list:
    data = base.http_get_json(API.format(slug=slug), {"content": "true"})
    out = []
    for j in (data or {}).get("jobs", []):
        title = j.get("title") or ""
        loc = (j.get("location") or {}).get("name") or ""
        if not base.title_is_relevant(title) or not base.location_in_scope(query, loc):
            continue
        out.append(base.normalize_posting(
            title=title, company=company, location=loc,
            description=base.html_to_text(j.get("content") or ""),
            url=j.get("absolute_url"), source="career_site",
            canonical_id=f"greenhouse:{slug}:{j.get('id')}", confidence=0.8))
    return out
