"""Arbeitnow — free, no-auth job-board API (mostly Germany/Austria/Switzerland).

One endpoint returns a recent page of postings (no server-side query), so we filter
client-side by the query terms + location. Good free discovery baseline.
Docs: https://www.arbeitnow.com/api/job-board-api
"""
from __future__ import annotations

from agent.sources import base

API = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 3


def search(query: base.JobQuery) -> list:
    out, seen = [], set()
    for page in range(1, _MAX_PAGES + 1):
        data = base.http_get_json(API, {"page": page})
        rows = (data or {}).get("data") or []
        if not rows:
            break
        for j in rows:
            title = j.get("title") or ""
            company = j.get("company_name") or ""
            loc = j.get("location") or ("Remote" if j.get("remote") else "")
            if not base.location_in_scope(query, loc):
                continue
            if not base.title_is_relevant(title):
                continue
            slug = j.get("slug") or j.get("url") or f"{company}:{title}"
            if slug in seen:
                continue
            seen.add(slug)
            if base.is_excluded_company(query, company):
                continue
            out.append(base.normalize_posting(
                title=title, company=company, location=loc,
                description=j.get("description") or "", url=j.get("url"),
                source="arbeitnow", canonical_id=f"arbeitnow:{slug}", confidence=0.7))
    return out
