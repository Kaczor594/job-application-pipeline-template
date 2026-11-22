"""Generic sitemap + schema.org JSON-LD career-site connector.

For career sites that aren't a named ATS but DO (a) list job-detail pages in a public
sitemap and (b) expose a schema.org JobPosting JSON-LD on each detail page — e.g.
Deloitte (job.deloitte.com, URLs like /job-{slug}-_{id}). Same robots-compliant shape
as the Phenom/Radancy connectors, but the job-URL pattern is supplied per employer so
one connector covers many sites.

Registry entry:
    {"company": "...", "platform": "jsonld", "base_url": "https://host",
     "job_url_re": "/job-.*-_\\\\d+$", "id_re": "-_(\\\\d+)$", "max_fetch": 120}
`sitemap` defaults to {base_url}/sitemap.xml. `id_re` (optional) extracts a stable id
for the canonical_id; without it the URL is used.
"""
from __future__ import annotations

import re

from agent.sources import base


def search(query: base.JobQuery, *, company: str, base_url: str, job_url_re: str,
           sitemap: str | None = None, id_re: str | None = None,
           max_fetch: int = 80, **_) -> list:
    sm = sitemap or base_url.rstrip("/") + "/sitemap.xml"
    job_re = re.compile(job_url_re)
    idp = re.compile(id_re) if id_re else None
    cand = [u for u in base.sitemap_urls(sm)
            if job_re.search(u) and base.url_maybe_relevant(u)]
    out = []
    for u in cand[:max_fetch]:
        ld = base.jsonld_jobposting(u)
        if not ld:
            continue
        m = idp.search(u) if idp else None
        ext = base.extraction_from_jsonld(
            ld, url=u, company=company, canonical_id=f"jsonld:{m.group(1) if m else u}")
        if base.title_is_relevant(ext.role_guess) and base.location_in_scope(query, ext.location or ""):
            out.append(ext)
    return out
