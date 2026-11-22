"""Personio — public XML job feed (very common for European SMBs and startups).

GET https://{slug}.jobs.personio.de/xml
Each <position> has id, office (location), name (title), and jobDescriptions.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from agent.sources import base

URL = "https://{slug}.jobs.personio.de/xml"


def search(query: base.JobQuery, *, slug: str, company: str, **_) -> list:
    xml = base.http_get_text(URL.format(slug=slug))
    if not xml:
        return []
    soup = BeautifulSoup(xml, "xml")
    out = []
    for pos in soup.find_all("position"):
        title = (pos.find("name").text if pos.find("name") else "").strip()
        office = (pos.find("office").text if pos.find("office") else "").strip()
        if not base.title_is_relevant(title) or not base.location_in_scope(query, office):
            continue
        desc = " ".join(v.text for v in pos.find_all("value") if v.text)
        pid = (pos.find("id").text if pos.find("id") else "").strip()
        out.append(base.normalize_posting(
            title=title, company=company, location=office,
            description=base.html_to_text(desc),
            url=f"https://{slug}.jobs.personio.de/job/{pid}" if pid else None,
            source="career_site", canonical_id=f"personio:{slug}:{pid}", confidence=0.8))
    return out
