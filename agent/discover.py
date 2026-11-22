"""Active sourcing: query job sources and insert deduped candidates as job rows.

Runs BETWEEN Gmail ingest and ranking in the nightly orchestrator. Discovered jobs
land in the same `jobs` table (source='aggregator'/'career_site') and from there the
existing rank → threshold → pipeline machinery handles them unchanged. Cost stays
bounded: discovery only feeds the cheap ranker; the NIGHTLY_CAP still gates the one
expensive step.
"""
from __future__ import annotations

from agent import config, db, ingest_common
from agent.sources import aggregator_adzuna, aggregator_arbeitnow, base, registry

# name -> search(query) -> list[JDExtraction]. Aggregators query the whole market;
# career-site connectors (registry) are queried per employer in _career_sites().
SOURCES = {
    "arbeitnow": aggregator_arbeitnow.search,
    "adzuna": aggregator_adzuna.search,
}


def _log(msg: str) -> None:
    print(f"   [discover] {msg}", flush=True)


def _career_sites(query) -> list:
    """Query each registered employer's ATS via its connector. Capped collectively at
    DISCOVER_MAX_PER_SOURCE so one large board can't crowd out the aggregators."""
    employers = registry.load()
    if not employers:
        return []
    found: list = []
    for e in employers:
        fn = registry.CONNECTORS[e["platform"]]
        try:
            hits = fn(query, **{k: v for k, v in e.items() if k != "platform"})
        except Exception as ex:  # noqa: BLE001 — one bad employer never sinks discovery
            locator = e.get("slug") or e.get("host") or e.get("base_url") or "?"
            _log(f"career_site {e['platform']}:{locator} failed ({ex})")
            continue
        if hits:
            _log(f"career_site {e['company']}: {len(hits)} candidate(s)")
        found.extend(hits)
    return found[:config.DISCOVER_MAX_PER_SOURCE]


def _dedupe_batch(extractions: list) -> list:
    """Collapse same-night cross-source duplicates. Uses the same fuzzy matcher as
    the cross-night DB de-dupe (so an anonymized aggregator listing merges with the
    named career-site posting of the same role), and when two collide keeps the one
    with a resolved (non-anonymized) employer."""
    kept: list = []                         # list of [extraction, signature]
    for e in extractions:
        sig = db.job_signature(e.company_guess, e.role_guess, e.location)
        if not sig[2]:                      # no role tokens — can't match, keep as-is
            kept.append([e, sig])
            continue
        dup = next((pair for pair in kept if db.signatures_match(sig, pair[1])), None)
        if dup is None:
            kept.append([e, sig])
        elif db.company_unresolved(dup[0].company_guess) and not sig[4]:
            dup[0], dup[1] = e, sig          # prefer the named-employer posting
    return [e for e, _ in kept]


def _sources(query) -> list[str]:
    """Aggregators to query. 'auto' = Adzuna where it covers your country (needs a
    free key, skipped otherwise) + Arbeitnow for DE/AT/CH."""
    names = config.DISCOVER_SOURCES
    if names != ["auto"]:
        return names
    auto = ["adzuna"]
    if (query.country_code or "").upper() in ("DE", "AT", "CH"):
        auto.insert(0, "arbeitnow")
    return auto


def discover(conn, *, dry_run: bool = False) -> int:
    """Query enabled sources, dedupe, and insert new candidates (unless dry_run).
    Returns the number of NEW jobs inserted (or that WOULD be, in dry_run)."""
    query = base.JobQuery.from_profile()
    _log(f"terms={query.terms} location={query.location!r} country={query.country_code!r} "
         f"remote={query.remote}")

    collected: list = []
    for name in _sources(query):
        fn = SOURCES.get(name)
        if fn is None:
            _log(f"{name}: unknown source — skipped")
            continue
        try:
            found = fn(query)
        except Exception as e:  # noqa: BLE001 — one bad source never sinks discovery
            _log(f"{name}: failed ({e})")
            continue
        found = found[:config.DISCOVER_MAX_PER_SOURCE]
        _log(f"{name}: {len(found)} candidate(s)")
        collected.extend(found)

    collected.extend(_career_sites(query))

    collected = _dedupe_batch(collected)[:config.DISCOVER_MAX_TOTAL]

    # Build the existing-jobs index ONCE for the whole batch (cross-source / cross-night
    # de-dupe), rather than re-scanning the table for every candidate.
    index = db.existing_job_index(conn)
    new = 0
    for ext in collected:
        # Skip if a job with the same normalized company+role already exists
        # (e.g. already arrived via email or a prior night).
        if db.match_existing_job(index, ext.company_guess, ext.role_guess, ext.location):
            continue
        if dry_run:
            new += 1
            _log(f"  + would add: {ext.company_guess} — {ext.role_guess}  [{ext.source}]")
            continue
        new += ingest_common.upsert_extraction(conn, ext)
    _log(f"{'(dry-run) ' if dry_run else ''}{new} new candidate(s) after dedupe/cap")
    return new


if __name__ == "__main__":   # quick offline-ish dry run: hits live APIs, writes nothing
    import sys
    conn = db.connect()
    db.migrate(conn)
    sys.exit(0 if discover(conn, dry_run=True) >= 0 else 1)
