"""SQLite persistence for the job agent.

Stdlib sqlite3, raw SQL, no ORM — matches the cbb-prediction-model /
chess-insights convention (row_factory=Row, PRAGMA foreign_keys=ON). The DB lives
at data/jobs.db (gitignored — it holds employer names and JD text).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent import config

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_hash           TEXT UNIQUE NOT NULL,
    company            TEXT,
    role               TEXT,
    location           TEXT,
    source             TEXT,           -- linkedin|career_page|recruiter_email|bootstrap|unknown
    jd_url             TEXT,
    jd_text_path       TEXT,
    jd_confidence      REAL,
    app_dir            TEXT,           -- applications/<slug>/ once the pipeline runs
    language           TEXT,
    relevance_score    INTEGER,        -- 0-100 from the Haiku ranker
    relevance_reason   TEXT,
    fit_percentage     INTEGER,        -- from fit_summary.json
    salary_mid         INTEGER,        -- from fit_summary.json salary_estimate.mid
    rating             TEXT,           -- 'up' | 'down' | NULL (latest; full log in feedback)
    applied            INTEGER NOT NULL DEFAULT 0,
    applied_date       TEXT,
    status             TEXT NOT NULL DEFAULT 'new',
                       -- new|listed|applied|confirmed|rejected|interview|excluded|error
    status_date        TEXT,
    last_status_msg_id TEXT,
    source_email_id    TEXT,
    first_seen         TEXT NOT NULL,
    last_seen          TEXT,
    notes              TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status    ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_relevance ON jobs(relevance_score);
CREATE INDEX IF NOT EXISTS idx_jobs_firstseen ON jobs(first_seen);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    rating      TEXT NOT NULL,
    company     TEXT, role TEXT, industry TEXT, role_level TEXT,
    reason      TEXT,           -- why: 'interest' (genuine dis/like) | 'qualification'
                                -- (passed only because under-qualified — NOT a relevance signal)
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_proposals (
    proposal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    proposed_status TEXT NOT NULL,                 -- confirmed|rejected|interview
    msg_id          TEXT, thread_id TEXT,
    evidence        TEXT, confidence REAL,
    resolved        INTEGER NOT NULL DEFAULT 0,    -- 0 pending | 1 accepted | -1 dismissed
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposals_resolved ON status_proposals(resolved);

CREATE TABLE IF NOT EXISTS processed_emails (
    msg_id       TEXT PRIMARY KEY,
    thread_id    TEXT,
    processed_at TEXT NOT NULL,
    kind         TEXT                              -- 'alert' | 'status'
);

CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date   TEXT NOT NULL,
    seen       INTEGER, ranked INTEGER, built INTEGER, errors INTEGER,
    notes      TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


# ── time / text helpers ─────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def normalize(text: Optional[str]) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for hashing/matching."""
    if not text:
        return ""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def job_hash(company: str, role: str, canonical_id: Optional[str] = None,
             location: Optional[str] = None) -> str:
    """Stable dedup key. Prefer a canonical id (e.g. a LinkedIn job id); else fall
    back to normalized company|role|location. Errs toward *specific* so two
    distinct roles never collide — a false 'new' is acceptable, a false merge
    (silently dropping a real job) is not."""
    cid = (canonical_id or "").strip().lower()
    if cid:
        raw = cid                       # a globally-unique id (e.g. LinkedIn job id) — dedupe on it alone
    else:
        raw = f"{normalize(company)}|{normalize(role)}|{normalize(location)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ── connection / migration ──────────────────────────────────────────────────────
def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)                 # creates missing tables (won't alter existing ones)
    # v1 -> v2: feedback.reason. Guarded so it's a no-op on fresh DBs (DDL already has it)
    # and idempotent on already-migrated ones.
    if not _column_exists(conn, "feedback", "reason"):
        conn.execute("ALTER TABLE feedback ADD COLUMN reason TEXT")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row is None or row["v"] is None or row["v"] < SCHEMA_VERSION:
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


# ── jobs: write ─────────────────────────────────────────────────────────────────
def job_exists(conn: sqlite3.Connection, jhash: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM jobs WHERE job_hash = ?", (jhash,)).fetchone() is not None


def insert_job(conn: sqlite3.Connection, *, jhash: str, company: str, role: str,
               location: Optional[str] = None, source: Optional[str] = None,
               jd_url: Optional[str] = None, jd_text_path: Optional[str] = None,
               jd_confidence: Optional[float] = None,
               source_email_id: Optional[str] = None,
               status: str = "new", notes: Optional[str] = None) -> tuple[int, bool]:
    """Insert a freshly-ingested job. If the hash already exists, touch last_seen
    and return (existing_id, False). Returns (job_id, created)."""
    existing = conn.execute(
        "SELECT job_id FROM jobs WHERE job_hash = ?", (jhash,)).fetchone()
    ts = now_iso()
    if existing:
        conn.execute("UPDATE jobs SET last_seen = ? WHERE job_id = ?",
                     (ts, existing["job_id"]))
        conn.commit()
        return existing["job_id"], False
    cur = conn.execute(
        """INSERT INTO jobs (job_hash, company, role, location, source, jd_url,
               jd_text_path, jd_confidence, source_email_id, status,
               first_seen, last_seen, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (jhash, company, role, location, source, jd_url, jd_text_path,
         jd_confidence, source_email_id, status, ts, ts, notes))
    conn.commit()
    return cur.lastrowid, True


def insert_bootstrap_job(conn: sqlite3.Connection, *, jhash: str, company: str,
                         role: str, location: Optional[str], app_dir: str,
                         language: Optional[str], fit_percentage: Optional[int],
                         salary_mid: Optional[int], applied_date: str,
                         notes: Optional[str] = None) -> tuple[int, bool]:
    """Import a historical application (status='applied'). Idempotent via job_hash."""
    existing = conn.execute(
        "SELECT job_id FROM jobs WHERE job_hash = ?", (jhash,)).fetchone()
    if existing:
        return existing["job_id"], False
    cur = conn.execute(
        """INSERT INTO jobs (job_hash, company, role, location, source, app_dir,
               language, fit_percentage, salary_mid, applied, applied_date,
               status, status_date, first_seen, last_seen, notes)
           VALUES (?,?,?,?,'bootstrap',?,?,?,?,1,?, 'applied',?,?,?,?)""",
        (jhash, company, role, location, app_dir, language, fit_percentage,
         salary_mid, applied_date, applied_date, applied_date, applied_date, notes))
    conn.commit()
    return cur.lastrowid, True


def set_relevance(conn, job_id: int, score: int, reason: str) -> None:
    conn.execute("UPDATE jobs SET relevance_score = ?, relevance_reason = ? WHERE job_id = ?",
                 (score, reason, job_id))
    conn.commit()


def attach_materials(conn, job_id: int, *, app_dir: str, company: str, role: str,
                     language: Optional[str], fit_percentage: Optional[int],
                     salary_mid: Optional[int], status: str = "listed") -> None:
    """Record the generated-materials dir + the authoritative fields apply.py
    derived (company/role/language/fit) after a successful pipeline run."""
    conn.execute(
        """UPDATE jobs SET app_dir = ?, company = ?, role = ?, language = ?,
               fit_percentage = ?, salary_mid = ?, status = ?, status_date = ?
           WHERE job_id = ?""",
        (app_dir, company, role, language, fit_percentage, salary_mid, status,
         now_iso(), job_id))
    conn.commit()


def set_status(conn, job_id: int, status: str, msg_id: Optional[str] = None) -> None:
    conn.execute(
        """UPDATE jobs SET status = ?, status_date = ?,
               last_status_msg_id = COALESCE(?, last_status_msg_id) WHERE job_id = ?""",
        (status, now_iso(), msg_id, job_id))
    conn.commit()


def mark_applied(conn, job_id: int) -> None:
    conn.execute(
        """UPDATE jobs SET applied = 1, applied_date = ?,
               status = CASE WHEN status IN ('new','listed') THEN 'applied' ELSE status END,
               status_date = ? WHERE job_id = ?""",
        (today_str(), now_iso(), job_id))
    conn.commit()


def mark_error(conn, job_id: int, msg: str) -> None:
    conn.execute("UPDATE jobs SET status = 'error', status_date = ?, notes = ? WHERE job_id = ?",
                 (now_iso(), msg[:500], job_id))
    conn.commit()


def set_rating(conn, job_id: int, rating: Optional[str],
               reason: Optional[str] = None) -> None:
    """Set the latest rating ('up'/'down'/None) and append to the feedback log
    (the append-only log is what feeds the relevance ranker).

    `reason` qualifies a 'down': 'interest' = genuine disinterest (penalize similar
    roles in ranking); 'qualification' = passed only because under-qualified, while
    still interested (must NOT penalize relevance — see preference_profile._exemplars)."""
    conn.execute("UPDATE jobs SET rating = ? WHERE job_id = ?", (rating, job_id))
    if rating in ("up", "down"):
        job = get_job(conn, job_id)
        conn.execute(
            """INSERT INTO feedback (job_id, rating, company, role, industry,
                   role_level, reason, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, rating, job["company"] if job else None,
             job["role"] if job else None, None, None, reason, now_iso()))
    conn.commit()


# ── jobs: read ──────────────────────────────────────────────────────────────────
def get_job(conn, job_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()


def jobs_needing_score(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE status = 'new' AND relevance_score IS NULL "
        "ORDER BY job_id").fetchall()


def jobs_above(conn, threshold: int) -> list[sqlite3.Row]:
    """New, un-pipelined jobs scoring at/above the relevance threshold, best first."""
    return conn.execute(
        "SELECT * FROM jobs WHERE status = 'new' AND relevance_score >= ? "
        "ORDER BY relevance_score DESC", (threshold,)).fetchall()


def jobs_new_ranked(conn) -> list[sqlite3.Row]:
    """All un-pipelined new jobs that have a relevance score, best first — regardless
    of threshold. Source pool for the minimum-daily-review top-up (we pipeline the best
    below-threshold backlog only when the standing review queue runs short)."""
    return conn.execute(
        "SELECT * FROM jobs WHERE status = 'new' AND relevance_score IS NOT NULL "
        "ORDER BY relevance_score DESC").fetchall()


def reviewable_jobs(conn, limit: Optional[int] = None) -> list[sqlite3.Row]:
    """Jobs with prepared documents still awaiting the user's review — materials
    generated (app_dir set / status 'listed'), not yet rated, not applied. Best
    relevance first. This is the standing queue the morning digest re-surfaces so it
    never looks empty just because few net-new roles came in overnight."""
    sql = ("SELECT * FROM jobs WHERE status = 'listed' AND app_dir IS NOT NULL "
           "AND app_dir != '' AND (rating IS NULL OR rating = '') "
           "AND COALESCE(applied, 0) = 0 "
           "ORDER BY relevance_score DESC NULLS LAST, first_seen DESC")
    if limit is not None:
        return conn.execute(sql + " LIMIT ?", (limit,)).fetchall()
    return conn.execute(sql).fetchall()


_LEGAL_NOISE = {"gmbh", "ag", "se", "kg", "kgaa", "mbh", "co", "ohg", "ug", "inc",
                "ltd", "llc", "plc", "group", "gruppe", "holding", "deutschland",
                "germany", "europe", "europa"}


def company_stem(name: str) -> str:
    """Normalized company with legal-form / locale noise stripped, so 'Bosch Group',
    'Bosch GmbH' and 'Bosch' share a stem for fuzzy matching."""
    return " ".join(t for t in normalize(name).split() if t not in _LEGAL_NOISE)


# German gender-marker tokens — '(m/w/d)', '(all genders)' etc. normalize to noise
# like 'm w d' that drags title comparison down; strip before matching.
_TITLE_NOISE = {"m", "w", "d", "f", "x", "gn", "div", "mwd", "wmd", "mfd", "fmd",
                "all", "genders", "gender"}

# Markers that mean "we don't actually know the employer" — anonymized aggregator
# feeds (e.g. Adzuna) or recruiter listings. When a company is unresolved we can't
# gate de-dupe on the company name, so we fall back to an identical specific title.
_UNRESOLVED_COMPANY = {"unknown", "confidential", "undisclosed", "anonymous",
                       "various", "company", "employer", "recruiter", "agency", "na"}

# Region/country noise stripped before comparing locations, so 'Berlin, Berlin
# (Stadt)' and 'Berlin, DE-BE, DEU' both reduce to the city token {berlin}. The
# country words come from your job_search.location (agent/market.py).
def _loc_noise() -> set:
    from agent import market
    return market.load().location_noise() | {"deu", "ger", "usa", "gbr", "uk"}


def _role_tokens(role: str) -> set:
    return set(normalize(role).split()) - _TITLE_NOISE


def _loc_tokens(location: Optional[str]) -> set:
    noise = _loc_noise()
    return {t for t in normalize(location).split() if len(t) > 1 and t not in noise}


def company_unresolved(name: Optional[str]) -> bool:
    """True when `name` doesn't actually identify an employer (anonymized feed,
    empty, or a hedged LLM guess like 'Unknown (likely Acme Corp)')."""
    stem = company_stem(name or "")
    if not stem:
        return True
    return stem.split()[0] in _UNRESOLVED_COMPANY or "likely" in stem


def job_signature(company: str, role: str, location: Optional[str] = None) -> tuple:
    """Precomputed match key: (company_stem, head_token, role_tokens, loc_tokens,
    company_unresolved)."""
    cs = company_stem(company)
    return (cs, cs.split()[0] if cs else "", _role_tokens(role),
            _loc_tokens(location), company_unresolved(company))


def signatures_match(a: tuple, b: tuple) -> bool:
    """Do two job_signature() tuples describe the same posting? Conservative — errs
    toward NOT merging (a near-duplicate listing is cheaper than silently dropping a
    genuinely distinct role)."""
    a_cs, a_head, a_rt, a_lt, a_unres = a
    b_cs, b_head, b_rt, b_lt, b_unres = b
    if not a_rt or not b_rt:
        return False
    union = a_rt | b_rt
    jac = len(a_rt & b_rt) / len(union) if union else 0.0
    if not a_unres and not b_unres:
        # Both employers known: shared company head + stem containment + role overlap.
        if a_head != b_head:
            return False
        if not (a_cs == b_cs or a_cs in b_cs or b_cs in a_cs):   # one stem contains the other
            return False
        return a_rt == b_rt or jac >= 0.7
    # Anonymized employer on at least one side: can't gate on company, so demand an
    # identical, specific (>= 4 distinct tokens) title AND — when both locations are
    # known — a shared city token. Keeps generic titles ('Data Analyst') from merging.
    if a_rt != b_rt or len(a_rt) < 4:
        return False
    if a_lt and b_lt and not (a_lt & b_lt):
        return False
    return True


def existing_job_index(conn) -> list[tuple]:
    """One scan of `jobs` -> precomputed (job_id, signature) so a discovery run does
    ONE table scan instead of one per candidate (was O(candidates × rows))."""
    idx = []
    for row in conn.execute("SELECT job_id, company, role, location FROM jobs"):
        sig = job_signature(row["company"], row["role"], row["location"])
        if sig[2]:                                      # has role tokens to match on
            idx.append((row["job_id"], sig))
    return idx


def match_existing_job(index: list[tuple], company: str, role: str,
                       location: Optional[str] = None) -> Optional[int]:
    """Best-effort existing-job lookup ACROSS sources against a prebuilt index,
    tolerant of company legal-form variants, minor title wording, and anonymized
    employers (see signatures_match)."""
    cand = job_signature(company, role, location)
    if not cand[2]:
        return None
    for job_id, sig in index:
        if signatures_match(cand, sig):
            return job_id
    return None


def job_id_by_company_role(conn, company: str, role: str,
                           location: Optional[str] = None) -> Optional[int]:
    """Single-shot convenience wrapper (builds the index, then matches). Hot loops
    should call existing_job_index() once and match_existing_job() per candidate."""
    return match_existing_job(existing_job_index(conn), company, role, location)


def jobs_seen_on(conn, day: str) -> list[sqlite3.Row]:
    """All jobs first seen on the given YYYY-MM-DD (for the nightly digest).

    Carries `down_reason` = the latest downvote reason ('interest' | 'qualification'
    | NULL) so the dashboard can show whether a Pass was a genuine-disinterest pass
    or a 'too senior' (qualification) pass."""
    return conn.execute(
        "SELECT j.*, ("
        "  SELECT f.reason FROM feedback f "
        "  WHERE f.job_id = j.job_id AND f.rating = 'down' "
        "  ORDER BY f.feedback_id DESC LIMIT 1) AS down_reason "
        "FROM jobs j WHERE substr(j.first_seen,1,10) = ? "
        "ORDER BY j.relevance_score DESC NULLS LAST, j.job_id", (day,)).fetchall()


def counts_by_status(conn) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def recent_feedback(conn, limit: int = 40) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM feedback ORDER BY feedback_id DESC LIMIT ?", (limit,)).fetchall()


# ── processed-email idempotency ─────────────────────────────────────────────────
def email_processed(conn, msg_id: str, kind: Optional[str] = None) -> bool:
    if kind:
        return conn.execute(
            "SELECT 1 FROM processed_emails WHERE msg_id = ? AND kind = ?",
            (msg_id, kind)).fetchone() is not None
    return conn.execute(
        "SELECT 1 FROM processed_emails WHERE msg_id = ?", (msg_id,)).fetchone() is not None


def mark_email_processed(conn, msg_id: str, kind: str,
                         thread_id: Optional[str] = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO processed_emails (msg_id, thread_id, processed_at, kind) "
        "VALUES (?,?,?,?)", (msg_id, thread_id, now_iso(), kind))
    conn.commit()


# ── status proposals (Phase C) ──────────────────────────────────────────────────
def insert_proposal(conn, *, job_id: int, proposed_status: str, msg_id: str,
                    thread_id: Optional[str], evidence: str, confidence: float) -> int:
    cur = conn.execute(
        """INSERT INTO status_proposals (job_id, proposed_status, msg_id, thread_id,
               evidence, confidence, created_at) VALUES (?,?,?,?,?,?,?)""",
        (job_id, proposed_status, msg_id, thread_id, evidence, confidence, now_iso()))
    conn.commit()
    return cur.lastrowid


def pending_proposals(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT p.*, j.company, j.role FROM status_proposals p
               JOIN jobs j ON j.job_id = p.job_id
           WHERE p.resolved = 0 ORDER BY p.created_at DESC""").fetchall()


def resolve_proposal(conn, proposal_id: int, accept: bool) -> Optional[sqlite3.Row]:
    prop = conn.execute(
        "SELECT * FROM status_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    if prop is None:
        return None
    conn.execute("UPDATE status_proposals SET resolved = ? WHERE proposal_id = ?",
                 (1 if accept else -1, proposal_id))
    if accept:
        set_status(conn, prop["job_id"], prop["proposed_status"], prop["msg_id"])
    conn.commit()
    return prop


# ── run log ─────────────────────────────────────────────────────────────────────
def record_run(conn, *, seen: int, ranked: int, built: int, errors: int,
               notes: str = "") -> None:
    conn.execute(
        "INSERT INTO runs (run_date, seen, ranked, built, errors, notes) "
        "VALUES (?,?,?,?,?,?)", (today_str(), seen, ranked, built, errors, notes))
    conn.commit()
