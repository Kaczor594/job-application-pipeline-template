"""Employer registry: which ATS each target company uses, so the career-site
connectors know where to look.

employers.json (repo root, gitignored — yours to edit) is a list of objects:
    {"company": "Display Name", "platform": "greenhouse|lever|personio|smartrecruiters",
     "slug": "ats-tenant-token"}

Start from agent/employers.example.json (used automatically until you create your
own). Unknown platforms / missing slugs are skipped, so a bad row never breaks a run.
See docs/EMPLOYERS.md for how to find a company's ATS and slug.
"""
from __future__ import annotations

import json

from agent import config
from agent.sources import (ats_greenhouse, ats_jsonld, ats_lever, ats_myjobshop,
                           ats_personio, ats_phenom, ats_radancy,
                           ats_smartrecruiters, ats_successfactors, ats_workday)

def _registry_path():
    return config.EMPLOYERS_JSON if config.EMPLOYERS_JSON.exists() else config.EMPLOYERS_EXAMPLE_JSON

# Connector locator fields by platform:
#   greenhouse/lever/personio/smartrecruiters -> {slug}
#   phenom/radancy                            -> {base_url}
#   workday                                   -> {host, tenant, site}
#   jsonld                                    -> {base_url, job_url_re} (+ id_re, max_fetch)
#   successfactors                            -> {base_url} (+ location, max_pages)
#   myjobshop                                 -> {base_url, tenant_id, jobshop_id, vanities}
# discover._career_sites passes the whole registry entry as kwargs.
CONNECTORS = {
    "greenhouse": ats_greenhouse.search,
    "lever": ats_lever.search,
    "personio": ats_personio.search,
    "smartrecruiters": ats_smartrecruiters.search,
    "phenom": ats_phenom.search,
    "radancy": ats_radancy.search,
    "workday": ats_workday.search,
    "jsonld": ats_jsonld.search,
    "successfactors": ats_successfactors.search,
    "myjobshop": ats_myjobshop.search,
}


def load() -> list[dict]:
    try:
        entries = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in entries
            if isinstance(e, dict) and e.get("platform") in CONNECTORS
            and e.get("company")
            and (e.get("slug") or e.get("base_url") or e.get("host"))]
