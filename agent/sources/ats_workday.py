"""Workday — the cxs JSON search API used by many large enterprises.

Search:  POST https://{host}/wday/cxs/{tenant}/{site}/jobs   {"searchText","limit","offset",...}

Registry entry needs host + tenant + site (the triple isn't guessable; read it off
the live careers URL https://{host}/{site}). The search listing carries title +
location but not the description (the cxs detail GET 406s), so — like
SmartRecruiters — discovery is title/location only and enrich() fetches the full JD
from the public URL if the job is shortlisted.
"""
from __future__ import annotations

from agent.sources import base

_LIMIT = 20
_MAX_TERMS = 6


def search(query: base.JobQuery, *, company: str, host: str, tenant: str,
           site: str, **_) -> list:
    cxs_jobs = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    seen: set = set()
    out: list = []
    for term in query.terms[:_MAX_TERMS]:
        d = base.http_post_json(cxs_jobs, {"appliedFacets": {}, "limit": _LIMIT,
                                           "offset": 0, "searchText": term})
        for jp in (d or {}).get("jobPostings", []):
            ep = jp.get("externalPath") or ""
            title = jp.get("title") or ""
            # Most tenants fill locationsText; some (e.g. Accenture) leave it null and put
            # the city in bulletFields[1:] (bulletFields[0] is the req id) or in the
            # externalPath itself (/job/{City}/{slug}_{id}). Fall back to those.
            bf = jp.get("bulletFields") or []
            loc = jp.get("locationsText") or (" ".join(str(x) for x in bf[1:]) if len(bf) > 1 else "")
            if not loc and ep:
                segs = ep.strip("/").split("/")
                if len(segs) >= 2:
                    loc = segs[1].replace("-", " ")
            if not ep or ep in seen:
                continue
            if not base.title_is_relevant(title) or not base.location_in_scope(query, loc):
                continue
            seen.add(ep)
            jid = ep.rstrip("/").split("_")[-1] or ep
            out.append(base.normalize_posting(
                title=title, company=company, location=loc, description="",
                url=f"https://{host}/{site}{ep}", source="career_site",
                canonical_id=f"workday:{tenant}:{jid}", confidence=0.55))
    return out
