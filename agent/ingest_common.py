"""Shared job-row insertion, used by BOTH the Gmail ingest and active discovery.

Factored out of nightly_agent.ingest so an extraction from any source (email,
aggregator API, ATS feed) becomes a job row through one identical, deduped path —
same job_hash, same JD-text-on-disk convention, same insert/touch semantics.
"""
from __future__ import annotations

from pathlib import Path

from agent import config, db
from agent.jd_extract import JDExtraction


def save_jd_text(text: str, jhash: str) -> Path:
    """Persist a job's JD text to data/jd_texts/<date>_<hash8>.txt (gitignored)."""
    config.JD_TEXTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.JD_TEXTS_DIR / f"{db.today_str()}_{jhash[:8]}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def upsert_extraction(conn, ext: JDExtraction, *, source_email_id: str | None = None) -> bool:
    """Insert one extraction as a job row, or touch last_seen if it already exists.
    Returns True iff a NEW job was created."""
    company = ext.company_guess or "Unknown"
    role = ext.role_guess or "Unknown role"
    jhash = db.job_hash(company, role, canonical_id=ext.canonical_id, location=ext.location)
    if db.job_exists(conn, jhash):
        db.insert_job(conn, jhash=jhash, company=company, role=role)   # touch last_seen
        return False
    jd_path = save_jd_text(ext.text, jhash)
    _, created = db.insert_job(
        conn, jhash=jhash, company=company, role=role, location=ext.location,
        source=ext.source, jd_url=ext.url, jd_text_path=str(jd_path),
        jd_confidence=ext.confidence, source_email_id=source_email_id,
        notes=ext.notes or None)
    return created
