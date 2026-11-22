"""Keep application statuses current from the inbox (Gmail add-on only).

Employer replies (confirmations, rejections, interview invites) are ordinary
mail, so this scans recent primary-inbox mail, fuzzy-matches each to an open job
(company-token overlap + sender domain), and classifies it with a cheap Haiku
call. It NEVER auto-applies a status change — it writes proposals that you accept
or dismiss on the dashboard. This is deliberate:
You may have applied to several roles at the same company, so attribution is a guess.
"""
from __future__ import annotations

import re
from typing import Optional

from agent import config, db, llm

OPEN_STATUSES = ("applied", "listed", "confirmed", "interview")
_GENERIC = {"gmbh", "der", "die", "das", "und", "the", "ltd", "inc", "group", "ag", "se",
            "deutschland", "careers", "jobs", "noreply", "reply", "mail", "talent",
            "recruiting", "team", "com", "www", "via", "bei", "people", "career",
            # ubiquitous job-search / role words — never distinctive enough to anchor a
            # company match (every "Senior Data Analyst" email would otherwise collide
            # with a company literally named e.g. "data-talent")
            "data", "analytics", "analyst", "analysts", "marketing", "senior", "junior",
            "manager", "consultant", "engineer", "engineering", "developer", "expert",
            "intern", "internship", "scientist", "science", "business", "intelligence",
            "digital", "specialist", "lead", "head", "office", "position", "application",
            "bewerbung", "stelle", "job", "undisclosed", "client"}
_KIND_TO_STATUS = {"confirmation": "confirmed", "rejection": "rejected",
                   "interview": "interview"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(t) > 2 and t not in _GENERIC}


def _match(msg, all_jobs, open_ids) -> Optional[dict]:
    """Best company match across ALL jobs by distinctive-token overlap with the
    sender + subject + domain. Conservative on two axes:

    * Attribution is only confident when the sender domain carries a company token
      OR at least two distinctive tokens overlap — a single common word is not enough.
    * The email's strongest match must be an OPEN job. If the best match is an
      already-resolved (rejected/closed) or not-yet-applied job, the email is about an
      application we are not tracking as open, so we skip rather than force it onto some
      other open job that merely shares a generic word.
    """
    m = re.search(r"@([\w.-]+)", msg.sender or "")
    domain = m.group(1).lower() if m else ""
    hay = _tokens(f"{msg.sender} {msg.subject} {msg.body[:500]} {domain.replace('.', ' ')}")
    best, best_key = None, (0, False, 0)  # (score, domain_hit, distinct-overlap count)
    for j in all_jobs:
        comp = _tokens(j["company"])
        if not comp:
            continue
        overlap = comp & hay
        domain_hit = any(t in domain for t in comp)
        key = (len(overlap) + (1 if domain_hit else 0), domain_hit, len(overlap))
        if key > best_key:
            best, best_key = j, key
    if best is None:
        return None
    _, domain_hit, n_overlap = best_key
    if not (domain_hit or n_overlap >= 2):
        return None  # too weak to attribute confidently
    if best["job_id"] not in open_ids:
        return None  # strongest match is an already-resolved / unapplied job
    return dict(best)


def _classify(msg) -> Optional[dict]:
    system = (
        "Classify ONE email about a job application. Output ONLY JSON: "
        '{"kind":"confirmation|rejection|interview|none","confidence":0..1,'
        '"reason":"<=12 words"}. confirmation = acknowledges receipt of an '
        "application; rejection = declines the candidate; interview = invites to an "
        "interview/call/assessment; none = anything else (job alerts, newsletters, "
        "marketing, unrelated).")
    user = f"From: {msg.sender}\nSubject: {msg.subject}\n\n{msg.body[:1500]}"
    try:
        data = llm.parse_json(llm.call(system, user, model=config.CLASSIFY_MODEL,
                                       max_tokens=120))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def scan_and_propose(conn, gmail, min_confidence: float = 0.6) -> int:
    all_jobs = conn.execute("SELECT * FROM jobs").fetchall()
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    open_ids = {r["job_id"] for r in conn.execute(
        f"SELECT job_id FROM jobs WHERE status IN ({placeholders})",
        OPEN_STATUSES).fetchall()}
    if not open_ids:
        return 0

    # -from:me excludes the agent's own digest (sent from you to you); employer
    # status mail is never self-sent. Job-alert mail is excluded too. The subject
    # guard below is belt-and-suspenders.
    query = (f'newer_than:{config.STATUS_SCAN_DAYS}d -{config.alert_query()} '
             f'-from:me category:primary')
    proposals = 0
    for mid in gmail.search(query, max_results=80):
        if db.email_processed(conn, mid, kind="status"):
            continue
        msg = gmail.get_message(mid)
        if (msg.subject or "").strip().startswith("Job agent —"):
            db.mark_email_processed(conn, mid, kind="status", thread_id=msg.thread_id)
            continue
        match = _match(msg, all_jobs, open_ids)
        if match is None:
            db.mark_email_processed(conn, mid, kind="status", thread_id=msg.thread_id)
            continue
        cls = _classify(msg)
        kind = (cls or {}).get("kind")
        conf = float((cls or {}).get("confidence") or 0)
        if kind in _KIND_TO_STATUS and conf >= min_confidence:
            db.insert_proposal(
                conn, job_id=match["job_id"], proposed_status=_KIND_TO_STATUS[kind],
                msg_id=mid, thread_id=msg.thread_id,
                evidence=f"{msg.subject[:110]} — {(cls.get('reason') or '')[:110]}",
                confidence=conf)
            proposals += 1
        db.mark_email_processed(conn, mid, kind="status", thread_id=msg.thread_id)
    return proposals
