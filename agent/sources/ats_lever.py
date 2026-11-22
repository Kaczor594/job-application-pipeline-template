"""Lever — public postings API (returns a JSON array).

GET https://api.lever.co/v0/postings/{slug}?mode=json
`slug` is the company token (e.g. 'leverdemo').
"""
from __future__ import annotations

from agent.sources import base

API = "https://api.lever.co/v0/postings/{slug}"


def search(query: base.JobQuery, *, slug: str, company: str, **_) -> list:
    data = base.http_get_json(API.format(slug=slug), {"mode": "json"})
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        title = j.get("text") or ""
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        if not base.title_is_relevant(title) or not base.location_in_scope(query, loc):
            continue
        out.append(base.normalize_posting(
            title=title, company=company, location=loc,
            description=j.get("descriptionPlain") or "", url=j.get("hostedUrl"),
            source="career_site", canonical_id=f"lever:{slug}:{j.get('id')}",
            confidence=0.8))
    return out
