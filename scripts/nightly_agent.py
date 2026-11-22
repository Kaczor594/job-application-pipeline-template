#!/usr/bin/env python3
"""Nightly orchestrator for the job-application agent.

Flow: find jobs (active discovery from job boards + your employer list, and —
if the Gmail add-on is on — job-alert emails) -> cheap bulk relevance rank ->
run apply.py on the relevant subset (gated by threshold + cap) -> [Gmail: scan
the inbox for status updates] -> write the morning digest (and email it with
Gmail on) -> mark mail processed.

Idempotent (job_hash dedup + processed-email labels) and cost-bounded (Haiku rank
+ a hard ceiling on pipeline runs). One bad job never aborts the night.

Usage:
  python scripts/nightly_agent.py                # full run
  python scripts/nightly_agent.py --no-pipeline  # ingest+rank+digest only (Phase A)
  python scripts/nightly_agent.py --dry-run      # ingest+rank, no pipeline, no send (writes file)
  python scripts/nightly_agent.py --no-send      # full run but write digest to a file
  python scripts/nightly_agent.py --limit N      # cap pipeline runs this run
  python scripts/nightly_agent.py --demo         # offline: synthetic alert emails, no Gmail, write file
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import (config, db, digest, discover, ingest_common, jd_extract,  # noqa: E402
                   pipeline_runner, preference_profile, relevance)


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── ingest ───────────────────────────────────────────────────────────────────────
def ingest(conn, messages) -> tuple[int, list[str]]:
    """Iterate alert emails (LAZILY — `messages` may be a generator, so we only
    fetch what we consume) and turn them into deduped job rows. Stops *before*
    starting a new email once MAX_JOBS_PER_NIGHT new jobs exist, leaving the rest
    un-handled so they're picked up on the next run (critical for a backlog).
    Returns (new_job_count, handled_message_ids)."""
    new = 0
    handled: list[str] = []
    for m in messages:
        if new >= config.MAX_JOBS_PER_NIGHT:
            log(f"  reached MAX_JOBS_PER_NIGHT ({config.MAX_JOBS_PER_NIGHT}) — "
                f"remaining messages deferred to the next run")
            break
        if db.email_processed(conn, m.id, kind="alert"):
            handled.append(m.id)
            continue
        try:
            extractions = jd_extract.extract_jobs(m, enrich=False)   # snippet-only; no web fetch
        except Exception as e:  # noqa: BLE001
            log(f"  extract failed for {m.id}: {e}")
            handled.append(m.id)              # skip a permanently-bad email
            continue
        for ext in extractions:
            new += ingest_common.upsert_extraction(conn, ext, source_email_id=m.id)
        handled.append(m.id)
    return new, handled


# ── rank ─────────────────────────────────────────────────────────────────────────
def _snippet_for(row) -> str:
    p = row["jd_text_path"]
    if p and Path(p).exists():
        try:
            return Path(p).read_text(encoding="utf-8")[:700]
        except OSError:
            pass
    return f"{row['role']} at {row['company']}"


def rank(conn, pref_ctx: str) -> int:
    pending = db.jobs_needing_score(conn)
    if not pending:
        return 0
    jobs = [{"company": r["company"], "role": r["role"], "location": r["location"],
             "snippet": _snippet_for(r)} for r in pending]
    scores = relevance.bulk_score(jobs, pref_ctx)
    for r, s in zip(pending, scores):
        db.set_relevance(conn, r["job_id"], s.score, s.reason)
    return len(pending)


# ── pipeline gate ────────────────────────────────────────────────────────────────
def _build_candidates(conn, candidates, cap: int, *, already_built: int = 0) -> tuple[int, int]:
    """Run apply.py over `candidates` (best-first) until the COMBINED build count
    (`already_built` + builds here) reaches `cap`. Returns (built_here, errors)."""
    built = errors = 0
    for row in candidates:
        if already_built + built >= cap:
            break
        if pipeline_runner.excluded_by_profile(row["company"]):
            db.set_status(conn, row["job_id"], "excluded")
            continue
        jd_path = row["jd_text_path"]
        if not jd_path or not Path(jd_path).exists():
            db.mark_error(conn, row["job_id"], "missing JD text file")
            errors += 1
            continue
        # Just-in-time: fetch the full posting for this shortlisted job (bounds web
        # scraping to the few jobs we actually pipeline, not the whole backlog).
        full = jd_extract.enrich(row["source"], row["jd_url"])
        if full and len(full) > 400:
            Path(jd_path).write_text(
                f"Role: {row['role']}\nCompany: {row['company']}\n\n{full}", encoding="utf-8")
            log(f"    enriched JD → {len(full)} chars")
        log(f"  pipeline → {row['company']} — {row['role']} (relevance {row['relevance_score']})")
        res = pipeline_runner.run(jd_path)
        if res.skipped:
            db.set_status(conn, row["job_id"], "excluded")
            continue
        if not res.ok:
            log(f"     ✗ {res.error}")
            db.mark_error(conn, row["job_id"], res.error or "pipeline failed")
            errors += 1
            continue
        ja, fit = res.jd_analysis, res.fit_summary
        db.attach_materials(
            conn, row["job_id"],
            app_dir=str(res.app_dir.relative_to(config.REPO_ROOT)),
            company=ja.get("company") or row["company"],
            role=ja.get("role_title") or row["role"],
            language=ja.get("language"),
            fit_percentage=fit.get("fit_percentage"),
            salary_mid=(fit.get("salary_estimate") or fit.get("salary_estimate_eur") or {}).get("mid"))
        log(f"     ✓ fit {fit.get('fit_percentage')}%  →  {res.app_dir.name}")
        built += 1
    return built, errors


def run_pipeline(conn, cap: int) -> tuple[int, int]:
    """Two-phase build (combined builds capped at `cap`):
      1. New jobs >= relevance threshold, best first (the genuine fresh qualifiers).
      2. Minimum-daily-review top-up: if the standing review queue (jobs WITH prepared
         docs, unrated + unapplied) is below MIN_REVIEW_TARGET, pipeline the best
         below-threshold backlog to fill the gap. Re-surfacing already-built jobs is
         free, so this only spends API budget when the queue has actually run dry.
    Returns (built, errors)."""
    primary = db.jobs_above(conn, config.RELEVANCE_THRESHOLD)
    built, errors = _build_candidates(conn, primary, cap)
    if built < len(primary):
        log(f"  nightly cap ({cap}) reached — {len(primary) - built} eligible roles deferred")

    # Phase 2 — top up to the daily review floor. reviewable_jobs() already counts
    # everything built in phase 1, so the deficit reflects the true remaining gap.
    deficit = config.MIN_REVIEW_TARGET - len(db.reviewable_jobs(conn))
    if deficit > 0 and built < cap:
        primary_ids = {r["job_id"] for r in primary}
        backlog = [r for r in db.jobs_new_ranked(conn)
                   if r["job_id"] not in primary_ids
                   and r["relevance_score"] < config.RELEVANCE_THRESHOLD]
        if backlog:
            topup_cap = min(cap, built + deficit)
            log(f"  review queue below target ({config.MIN_REVIEW_TARGET}); "
                f"topping up up to {topup_cap - built} from backlog")
            more, more_err = _build_candidates(conn, backlog, topup_cap, already_built=built)
            built += more
            errors += more_err
    return built, errors


# ── demo fixtures (offline test) ─────────────────────────────────────────────────
def _demo_messages():
    from agent.gmail_client import Message
    li_html = """<table>
      <tr><td><a href="https://www.linkedin.com/comm/jobs/view/3990000001/?trk=eml">
        Junior Data Analyst</a><p>Northwind Mobility &middot; Berlin &middot; 1d</p></td></tr>
      <tr><td><a href="https://www.linkedin.com/comm/jobs/view/3990000002/?trk=eml">
        Head of Data (m/w/d)</a><p>Lindenhof Versicherung &middot; Hamburg &middot; 3d</p></td></tr>
      <tr><td><a href="https://www.linkedin.com/comm/jobs/view/3990000003/?trk=eml">
        Warehouse Associate</a><p>Example Logistics &middot; Berlin &middot; 2d</p></td></tr>
    </table>"""
    li_links = [f"https://www.linkedin.com/comm/jobs/view/399000000{i}/?trk=eml"
                for i in (1, 2, 3)]
    recruiter_body = ("Company: Brightline Analytics\nRole: Data Consultant (m/w/d)\n\n"
                      "Responsibilities: deliver BI and analytics projects for clients, "
                      "SQL, Python, Power BI. Requirements: 2+ years, German+English. "
                      "We offer hybrid work in Berlin and training. " * 4)
    return [
        Message("demo-li", "t1", "LinkedIn <jobalerts-noreply@linkedin.com>",
                "3 new jobs for data analyst", "", text="", html=li_html, links=li_links),
        Message("demo-rec", "t2", "Recruiter <jobs@brightline.example>",
                "Data Consultant at Brightline Analytics", "", text=recruiter_body, html="", links=[]),
    ]


# ── main ─────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-pipeline", action="store_true", help="skip apply.py runs")
    ap.add_argument("--no-status", action="store_true", help="skip the inbox status scan")
    ap.add_argument("--no-send", action="store_true", help="build digest but don't email it")
    ap.add_argument("--dry-run", action="store_true",
                    help="ingest+rank only; no pipeline, no send")
    ap.add_argument("--limit", type=int, default=None, help="cap pipeline runs this run")
    ap.add_argument("--demo", action="store_true",
                    help="offline: synthetic alert emails, no Gmail, write digest to a file")
    ap.add_argument("--no-discover", action="store_true", help="skip active discovery this run")
    args = ap.parse_args()

    conn = db.connect()
    db.migrate(conn)
    log(f"=== nightly run {db.today_str()} (demo={args.demo}, dry_run={args.dry_run}) ===")

    gmail = None
    msg_source: list = []
    if args.demo:
        msg_source = _demo_messages()
    elif config.GMAIL_ENABLED:
        from agent import gmail_client
        ok, why = gmail_client.token_healthy()
        if ok:
            gmail = gmail_client.GmailClient()
            ids = gmail.list_unprocessed_job_messages(max_results=config.MAX_JOBS_PER_NIGHT * 5)
            where = f"label '{config.JOB_LABEL}'" if config.JOB_LABEL else "job-alert senders"
            log(f"{len(ids)} unprocessed alert message(s) ({where})")
            msg_source = (gmail.get_message(i) for i in ids)   # lazy: only fetched as consumed
        else:
            # Non-fatal: carry on with discovery only, leave a visible breadcrumb.
            log(f"GMAIL AUTH PROBLEM (continuing without Gmail): {why}")
            _emergency_notice(why)
    else:
        log("Gmail add-on off — jobs come from active discovery only")

    seen, handled_ids = ingest(conn, msg_source)
    log(f"ingest: {seen} new job(s) from {len(handled_ids)} message(s) handled")

    discovered = 0
    if config.DISCOVER_ENABLED and not args.no_discover and not args.demo:
        try:
            discovered = discover.discover(conn)
            log(f"discover: {discovered} new job(s) from active sourcing")
        except Exception as e:  # noqa: BLE001 — discovery must never abort the night
            log(f"discover failed (non-fatal): {e}")

    pref = preference_profile.load_or_build(conn)
    ranked = rank(conn, preference_profile.as_ranking_context(pref))
    log(f"ranked: {ranked} job(s)")

    built = errors = 0
    if not args.no_pipeline and not args.dry_run:
        cap = args.limit if args.limit is not None else config.NIGHTLY_CAP
        built, errors = run_pipeline(conn, cap)
        log(f"pipeline: {built} built, {errors} error(s)")

    if gmail and not args.no_status and not args.dry_run:
        try:
            from agent import status_tracker
            n = status_tracker.scan_and_propose(conn, gmail)
            log(f"status scan: {n} proposal(s)")
        except Exception as e:  # noqa: BLE001
            log(f"status scan failed (non-fatal): {e}")

    subject, html = digest.build(conn)
    run_notes = "demo" if args.demo else ""
    if discovered:
        run_notes = f"{run_notes} +{discovered} discovered".strip()
    db.record_run(conn, seen=seen, ranked=ranked, built=built, errors=errors,
                  notes=run_notes)

    # Always keep the latest digest on disk (the dashboard links to it).
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.DIGEST_FILE.write_text(html, encoding="utf-8")

    to = config.digest_to()
    if gmail and to and not args.dry_run and not args.no_send:
        gmail.send_html(to, subject, html)
        log(f"digest emailed to {to}: {subject}")
        for mid in handled_ids:
            try:
                gmail.add_processed_label(mid)
                db.mark_email_processed(conn, mid, kind="alert")
            except Exception as e:  # noqa: BLE001
                log(f"  could not label {mid} processed: {e}")
    else:
        log(f"digest written to {config.DIGEST_FILE}  (subject: {subject})")

    conn.close()
    log("=== run complete ===")


def _emergency_notice(why: str) -> None:
    """If the token is dead we can't send via Gmail; leave a breadcrumb so the
    failure isn't silent. (A future enhancement could send via SMTP as a fallback.)"""
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (config.LOG_DIR / "AUTH_PROBLEM.txt").write_text(
            f"{datetime.now().isoformat()}\nGmail auth failed:\n{why}\n"
            f"Fix: ./scripts/manage.sh authorize\n", encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    main()
