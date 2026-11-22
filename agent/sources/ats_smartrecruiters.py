"""SmartRecruiters — public postings API.

GET https://api.smartrecruiters.com/v1/companies/{slug}/postings
The listing carries title + location but not the full description (that needs a
per-posting call), so confidence is left modest; enrich() fetches the full JD from
the public URL if the job is shortlisted.
"""
from __future__ import annotations

from agent.sources import base

API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"


def _location(j: dict) -> str:
    loc = j.get("location") or {}
    if loc.get("remote"):
        return "Remote"
    return " ".join(p for p in (loc.get("city"), loc.get("region")) if p)


def search(query: base.JobQuery, *, slug: str, company: str, **_) -> list:
    data = base.http_get_json(API.format(slug=slug), {"limit": 100})
    out = []
    for j in (data or {}).get("content", []):
        title = j.get("name") or ""
        loc = _location(j)
        if not base.title_is_relevant(title) or not base.location_in_scope(query, loc):
            continue
        jid = j.get("id") or ""
        out.append(base.normalize_posting(
            title=title, company=company, location=loc,
            description="",   # listing has no body; enrich() fills it at pipeline time
            url=f"https://jobs.smartrecruiters.com/{slug}/{jid}" if jid else None,
            source="career_site", canonical_id=f"smartrecruiters:{slug}:{jid}",
            confidence=0.55))
    return out
