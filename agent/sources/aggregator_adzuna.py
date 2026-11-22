"""Adzuna — official jobs API (19 countries), keyword + location search with salary data.

Credentials (app_id/app_key) come from config.adzuna_credentials(); if unset the
source is simply skipped. One request per search term (free tier is rate-limited).
Docs: https://developer.adzuna.com/
"""
from __future__ import annotations

from agent import config
from agent.sources import base

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
# Countries Adzuna covers (ISO-2, lower case; the UK is "gb").
COUNTRIES = {"at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb", "in", "it",
             "mx", "nl", "nz", "pl", "sg", "us", "za"}


def country_for(code: str) -> str | None:
    c = (code or "").lower()
    c = {"uk": "gb"}.get(c, c)
    return c if c in COUNTRIES else None
_RESULTS_PER_TERM = 20


def _salary_note(j: dict) -> str:
    lo, hi = j.get("salary_min"), j.get("salary_max")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > 0:
        return f"Advertised salary (Adzuna estimate): {int(lo):,}–{int(hi):,} gross/year (local currency)."
    return ""


def search(query: base.JobQuery) -> list:
    creds = config.adzuna_credentials()
    if not creds:
        return []
    app_id, app_key = creds
    country = country_for(query.country_code)
    if not country:
        return []
    url = API.format(country=country)
    seen: set = set()
    groups: list[list] = []          # one list of extractions per search term
    for term in query.terms:
        data = base.http_get_json(url, {
            "app_id": app_id, "app_key": app_key, "what": term,
            "where": query.location, "results_per_page": _RESULTS_PER_TERM,
            "content-type": "application/json",
        })
        group = []
        for j in (data or {}).get("results", []):
            jid = str(j.get("id") or "")
            if not jid or jid in seen:
                continue
            seen.add(jid)
            company = (j.get("company") or {}).get("display_name") or ""
            loc = (j.get("location") or {}).get("display_name") or ""
            if not base.title_is_relevant(j.get("title") or ""):
                continue
            if base.is_excluded_company(query, company):
                continue
            if not base.location_in_scope(query, loc):
                continue
            group.append(base.normalize_posting(
                title=j.get("title") or "", company=company, location=loc,
                description=j.get("description") or "", url=j.get("redirect_url"),
                source="adzuna", canonical_id=f"adzuna:{jid}", confidence=0.7,
                salary_note=_salary_note(j)))
        groups.append(group)

    # Round-robin across terms so the downstream cap keeps a DIVERSE mix across
    # your search terms, not just the first term's results.
    out = []
    for i in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if i < len(g):
                out.append(g[i])
    return out
