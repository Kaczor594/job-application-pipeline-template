#!/usr/bin/env python3
"""One-time import of the existing applications/ directories into the status DB.

Each past application becomes a job row with status='applied' (you generated
materials for it ⇒ he applied/intended to). This (a) seeds the status history and
(b) provides the bootstrap signal for the relevance preference profile, and (c)
pre-populates the dedup layer so a re-surfaced old role isn't reprocessed.

Idempotent via the job_hash UNIQUE constraint — safe to re-run. Tolerates dirs
missing fit_summary.json. Run this BEFORE the first nightly run.

Usage:
  python scripts/bootstrap_db.py            # import
  python scripts/bootstrap_db.py --dry-run  # preview only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import config, db  # noqa: E402

DATE_RE = re.compile(r"^(\d{8})_")


def parse_applied_date(dirname: str, dir_path: Path) -> str:
    """Date from the leading YYYYMMDD in the dir name, else the dir's mtime."""
    m = DATE_RE.match(dirname)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return datetime.fromtimestamp(dir_path.stat().st_mtime).strftime("%Y-%m-%d")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="preview, don't write")
    args = ap.parse_args()

    if not config.APPLICATIONS_DIR.exists():
        print(f"No applications dir at {config.APPLICATIONS_DIR}")
        return

    conn = db.connect()
    db.migrate(conn)

    app_dirs = sorted(d for d in config.APPLICATIONS_DIR.iterdir() if d.is_dir())
    imported = already = skipped = no_fit = 0

    for d in app_dirs:
        ja = load_json(d / "jd_analysis.json")
        if not ja or not ja.get("company"):
            skipped += 1
            continue
        company = ja["company"]
        role = ja.get("role_title") or "Unknown role"
        location = ja.get("location")
        language = ja.get("language")

        fit = load_json(d / "fit_summary.json") or {}
        if not fit:
            no_fit += 1
        fit_pct = fit.get("fit_percentage")
        salary_mid = (fit.get("salary_estimate") or fit.get("salary_estimate_eur") or {}).get("mid")

        applied_date = parse_applied_date(d.name, d)
        jhash = db.job_hash(company, role, location=location)

        if args.dry_run:
            print(f"  would import: {company} — {role}  ({applied_date}, fit={fit_pct})")
            continue

        _, created = db.insert_bootstrap_job(
            conn, jhash=jhash, company=company, role=role, location=location,
            app_dir=str(d.relative_to(config.REPO_ROOT)), language=language,
            fit_percentage=fit_pct, salary_mid=salary_mid, applied_date=applied_date,
            notes="imported from applications/")
        imported += created
        already += (not created)

    print(f"\nBootstrap {'(dry run) ' if args.dry_run else ''}complete:")
    print(f"  imported:           {imported}")
    print(f"  already present:    {already}")
    print(f"  skipped (no jd):    {skipped}")
    print(f"  missing fit_summary:{no_fit}")
    if not args.dry_run:
        print("  status counts:", db.counts_by_status(conn))
    conn.close()


if __name__ == "__main__":
    main()
