#!/usr/bin/env python3
"""Check that profile.json has what the pipeline and the agent need.

Usage:
  python scripts/check_profile.py                 # checks ./profile.json
  python scripts/check_profile.py path/to/file.json

Prints problems (must fix) and warnings (worth a look), then OK or FAIL.
Exit code 0 = no problems. Standard library only, so it runs before setup.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def check(prof: dict, *, is_real_profile: bool) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []

    def need(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    def want(cond: bool, msg: str) -> None:
        if not cond:
            warnings.append(msg)

    personal = prof.get("personal") or {}
    need(bool(personal.get("name")), "personal.name is missing")
    need(bool(personal.get("email")), "personal.email is missing")
    city = personal.get("city")
    need(isinstance(city, dict) and bool(city.get("en")),
         'personal.city must be {"en": "...", "de": "..."} (the "en" value is required)')
    want(bool(personal.get("phone")), "personal.phone is empty (shown in the resume header)")
    want(bool(personal.get("languages")), "personal.languages is empty (shown in the resume header)")

    if is_real_profile and (personal.get("name") == "Jane Doe"
                            or "example.com" in str(personal.get("email", ""))):
        problems.append("this is still the example profile (Jane Doe) — replace it with your own data")

    exp = prof.get("experience")
    need(isinstance(exp, list) and len(exp) > 0, "experience must be a non-empty list")
    for i, e in enumerate(exp or []):
        tag = f"experience[{i}]"
        if not isinstance(e, dict):
            problems.append(f"{tag} is not an object")
            continue
        need(bool(e.get("company")), f"{tag}.company is missing")
        need(bool(e.get("title")), f"{tag}.title is missing")
        start, end = str(e.get("start", "")), str(e.get("end", ""))
        need(bool(re.fullmatch(r"\d{4}-\d{2}", start)), f"{tag}.start must look like 2023-04 (got {start!r})")
        need(end == "present" or bool(re.fullmatch(r"\d{4}-\d{2}", end)),
             f"{tag}.end must be YYYY-MM or 'present' (got {end!r})")
        facts = [k for k in ("achievements", "responsibilities", "highlights", "bullets") if e.get(k)]
        want(bool(facts), f"{tag} ({e.get('company')}) has no achievements/responsibilities — "
                          "the resume can only use facts that are written down here")

    edu = prof.get("education")
    want(isinstance(edu, list) and len(edu) > 0, "education is empty")
    want(bool(prof.get("skills")), "skills is empty")

    js = prof.get("job_search")
    if not isinstance(js, dict):
        warnings.append("job_search is missing — the nightly agent needs it (location, target roles)")
        return problems, warnings

    loc = js.get("location") or {}
    need(bool(loc.get("preferred") or loc.get("country")),
         "job_search.location needs at least 'preferred' (city) or 'country'")
    remote = loc.get("remote")
    need(remote in (None, "none", "country", "any"),
         f"job_search.location.remote must be none, country or any (got {remote!r})")
    code = loc.get("country_code")
    need(code is None or bool(re.fullmatch(r"[A-Za-z]{2}", str(code))),
         f"job_search.location.country_code must be a 2-letter code like DE (got {code!r})")
    want(bool(code), "job_search.location.country_code is empty (needed for job-board discovery)")

    sal = js.get("salary") or {}
    mn = sal.get("minimum", sal.get("minimum_eur"))
    need(mn is None or isinstance(mn, (int, float)), "job_search.salary.minimum must be a number")
    cur = sal.get("currency")
    need(cur is None or bool(re.fullmatch(r"[A-Z]{3}", str(cur))),
         f"job_search.salary.currency must be a 3-letter code like EUR (got {cur!r})")

    want(bool(js.get("target_roles")), "job_search.target_roles is empty (used for ranking + discovery)")
    want(bool(js.get("search_terms") or js.get("target_roles")),
         "job_search.search_terms is empty (discovery falls back to target_roles)")

    bands = js.get("salary_reference_bands") or {}
    for role, b in bands.items():
        ok = isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) for k in ("low", "high"))
        need(ok, f"job_search.salary_reference_bands[{role!r}] must be {{\"low\": n, \"high\": n}}")

    wa = js.get("work_authorization") or {}
    need(not wa or isinstance(wa.get("needs_sponsorship", False), bool),
         "job_search.work_authorization.needs_sponsorship must be true or false")
    return problems, warnings


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "profile.json"
    if not path.exists():
        print(f"FAIL — {path} not found. Copy profile.example.json to profile.json and fill it in.")
        return 1
    try:
        prof = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL — {path.name} is not valid JSON: line {e.lineno}, column {e.colno}: {e.msg}")
        return 1
    problems, warnings = check(prof, is_real_profile=path.name == "profile.json")
    for p in problems:
        print(f"  ✗ {p}")
    for w in warnings:
        print(f"  ! {w}")
    print("OK" if not problems else f"FAIL — {len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
