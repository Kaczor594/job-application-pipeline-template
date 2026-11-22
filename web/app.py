"""Companion local review dashboard (Flask) for the job agent.

Local-only: binds 127.0.0.1 with no auth — it serves personal PDFs and writes the
status DB, so it must never be exposed. Mirrors the chess-insights Flask
conventions (get_db on g, row_factory=Row, teardown_appcontext).

Run via the scheduler (./scripts/manage.sh start) or directly:
    .venv/bin/python web/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import (Flask, abort, g, jsonify, render_template,  # noqa: E402
                   request, send_from_directory)

from agent import config, db  # noqa: E402

app = Flask(__name__)

ALLOWED_FILES = {"resume.pdf", "cover_letter.pdf", "fit_summary.md",
                 "resume.tex", "cover_letter.tex"}
VALID_STATUS = {"new", "listed", "applied", "confirmed", "rejected",
                "interview", "excluded", "error"}


@app.context_processor
def _template_globals():
    from agent import market
    return {"cur": market.currency_symbol(), "gmail_enabled": config.GMAIL_ENABLED}


@app.route("/digest")
def latest_digest():
    """The most recent nightly digest (written every run, emailed only with Gmail on)."""
    if not config.DIGEST_FILE.exists():
        return "No digest yet — it appears after the first nightly run.", 404
    return config.DIGEST_FILE.read_text(encoding="utf-8")


def get_db():
    if "db" not in g:
        g.db = db.connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ── pages ────────────────────────────────────────────────────────────────────────
def _rank_key(job):
    """Blend relevance + fit for the 'Ready to review' ordering (see
    config.RANK_FIT_WEIGHT). Falls back to whichever score is present so a job
    missing one number still sorts sensibly; both-missing sinks to the bottom."""
    rel, fit = job["relevance_score"], job["fit_percentage"]
    if rel is None and fit is None:
        return -1.0
    if fit is None:
        return float(rel)
    if rel is None:
        return float(fit)
    w = config.RANK_FIT_WEIGHT
    return w * fit + (1.0 - w) * rel


@app.route("/")
def dashboard():
    conn = get_db()
    today = db.today_str()
    tonight = db.jobs_seen_on(conn, today)
    # "Ready to review" is the STANDING queue of prepared-but-unreviewed jobs (not just
    # tonight's builds), so a quiet sourcing night still surfaces older prepared roles
    # awaiting a decision. Blended relevance+fit ordering preserved via _rank_key.
    listed = sorted(db.reviewable_jobs(conn), key=_rank_key, reverse=True)
    return render_template(
        "dashboard.html", tonight=tonight, listed=listed,
        proposals=db.pending_proposals(conn), counts=db.counts_by_status(conn),
        today=today)


@app.route("/jobs")
def all_jobs():
    conn = get_db()
    status = request.args.get("status")
    sql = "SELECT * FROM jobs"
    params: tuple = ()
    if status in VALID_STATUS:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY COALESCE(applied_date, first_seen) DESC LIMIT 1000"
    return render_template("all_jobs.html", jobs=conn.execute(sql, params).fetchall(),
                           status=status, counts=db.counts_by_status(conn))


# ── api ──────────────────────────────────────────────────────────────────────────
def _payload():
    return request.get_json(force=True, silent=True) or request.form


@app.route("/api/rate", methods=["POST"])
def api_rate():
    # POST-only by design: a mutating action must never be reachable via GET, or
    # email/link prefetchers (e.g. Gmail) would trigger it. All rating is done from
    # the dashboard JS; the email only links here for a human click.
    data = _payload()
    job_id = int(data.get("job_id"))
    rating = data.get("rating")
    if rating == "clear":
        rating = None
    if rating not in (None, "up", "down"):
        abort(400)
    # reason qualifies a 'down': 'qualification' (e.g. too senior — interested but
    # under-qualified) is excluded from the ranker's "penalize similar" signal, so it
    # never suppresses junior roles; 'interest'/None is a genuine-disinterest pass.
    reason = data.get("reason")
    if reason not in (None, "interest", "qualification"):
        abort(400)
    db.set_rating(get_db(), job_id, rating, reason=reason if rating == "down" else None)
    return jsonify(ok=True, job_id=job_id, rating=rating, reason=reason)


@app.route("/api/apply", methods=["POST"])
def api_apply():
    db.mark_applied(get_db(), int(_payload().get("job_id")))
    return jsonify(ok=True)


@app.route("/api/status", methods=["POST"])
def api_status():
    data = _payload()
    status = data.get("status")
    if status not in VALID_STATUS:
        abort(400)
    db.set_status(get_db(), int(data.get("job_id")), status)
    return jsonify(ok=True)


@app.route("/api/proposal", methods=["POST"])
def api_proposal():
    # POST-only (see api_rate): never resolve a status proposal from a GET, so a
    # prefetcher can't silently accept/dismiss it. Resolved from the dashboard only.
    data = _payload()
    pid = int(data.get("proposal_id"))
    action = data.get("action")
    if action not in ("accept", "dismiss"):
        abort(400)
    db.resolve_proposal(get_db(), pid, accept=(action == "accept"))
    return jsonify(ok=True)


@app.route("/files/<int:job_id>/<path:name>")
def files(job_id, name):
    if name not in ALLOWED_FILES:
        abort(404)
    job = db.get_job(get_db(), job_id)
    if not job or not job["app_dir"]:
        abort(404)
    directory = (config.REPO_ROOT / job["app_dir"]).resolve()
    if not str(directory).startswith(str(config.REPO_ROOT.resolve())):
        abort(403)
    return send_from_directory(directory, name)


if __name__ == "__main__":
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, use_reloader=False)
