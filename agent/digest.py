"""Build the morning digest (HTML) — same neutral theme as the dashboard.

Email clients strip <link>/@import and web fonts, so everything is INLINE with
hardcoded token values and email-safe font stacks. The digest is always written
to data/digest-latest.html (viewable at /digest on the dashboard) and is emailed
only when the Gmail add-on is on.

IMPORTANT: the email contains NO state-mutating links. Email clients (Gmail) prefetch
links, which would auto-trigger any GET action. So every action — rate, mark applied,
accept/dismiss a status proposal — happens on the dashboard, which the email links to
(plain, safe page views). This matches the "email + companion page" design.

Sections: (A) ready to review, (B) everything seen tonight, (C) status proposals.
"""
from __future__ import annotations

import html
import json
from typing import Optional

from agent import config, db, market

# ── theme tokens (hardcoded for email; mirror web/static/css/tokens.css) ─────────
PAPER = "#FFFFFF"
PAGE = "#F6F7F8"
SUNKEN = "#EEF0F2"
INK = "#1F2933"
FG2 = "#3E4C59"
FG3 = "#6B7785"
LINE = "#E4E7EB"
LINE2 = "#D3D8DE"
MOSS = "#1F7A6B"
MOSS_BTN = "#2F9C8A"
MOSS_DK = "#17594E"
MOSS_SOFT = "#E3F4F1"
MOSS_BORDER = "#B8E2DA"
TERRA = "#B8661A"
TERRA_DK = "#7A4310"

SERIF = "Georgia, 'Times New Roman', serif"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
TNUM = "font-variant-numeric:tabular-nums;"


def _salary_floor() -> Optional[int]:
    try:
        prof = json.loads(config.PROFILE_JSON.read_text(encoding="utf-8"))
        sal = prof.get("job_search", {}).get("salary", {}) or {}
        return sal.get("minimum", sal.get("minimum_eur"))
    except (OSError, json.JSONDecodeError):
        return None


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _badge(text: str, bg: str, fg: str, border: str) -> str:
    return (f'<span style="display:inline-block;font-family:{MONO};font-size:10px;'
            f'font-weight:600;letter-spacing:.06em;text-transform:uppercase;{TNUM}'
            f'padding:2px 7px;border-radius:2px;background:{bg};color:{fg};'
            f'border:1px solid {border};">{text}</span>')


def _link(href: str, text: str) -> str:
    return f'<a href="{href}" style="color:{MOSS};text-decoration:none">{text}</a>'


def _btn(href: str, text: str) -> str:
    return (f'<a href="{href}" style="display:inline-block;font-family:{SANS};'
            f'font-size:13px;font-weight:600;color:{PAPER};background:{MOSS_BTN};'
            f'border:1px solid {MOSS};border-bottom-color:{MOSS_DK};border-radius:4px;'
            f'padding:8px 15px;text-decoration:none">{text}</a>')


def _salary_note(salary_mid: Optional[int], floor: Optional[int]) -> str:
    if not salary_mid:
        return ""
    color = TERRA_DK if (floor and salary_mid < floor) else MOSS
    tail = " · below floor" if (floor and salary_mid < floor) else ""
    sym = market.currency_symbol()
    return f'<span style="font-family:{MONO};{TNUM}color:{color}">~{sym}{salary_mid:,}{tail}</span>'


def _h2(text: str, note: str = "") -> str:
    extra = (f' <span style="font-family:{SANS};font-weight:400;font-size:13px;'
             f'color:{FG3}">{note}</span>') if note else ""
    return (f'<h2 style="font-family:{SANS};font-size:16px;font-weight:600;'
            f'letter-spacing:-.01em;color:{INK};margin:26px 0 10px;">{text}{extra}</h2>')


def _section_ready(ready, base, floor) -> str:
    """`ready` = the standing review queue (jobs with prepared docs, unrated +
    unapplied), already filtered by db.reviewable_jobs()."""
    listed = list(ready)
    if not listed:
        return (f'<p style="font-family:{SANS};font-size:13px;color:{FG3};margin:0">'
                f'Nothing awaiting review — every prepared role has been rated or '
                f'applied to. New roles will appear here as they are sourced.</p>')
    blocks = []
    for j in listed:
        jid = j["job_id"]
        badges = []
        if j["fit_percentage"] is not None:
            badges.append(_badge(f"fit {j['fit_percentage']}%", MOSS_SOFT, MOSS_DK, MOSS_BORDER))
        if j["relevance_score"] is not None:
            badges.append(_badge(f"rel {j['relevance_score']}", SUNKEN, FG2, LINE2))
        sal = _salary_note(j["salary_mid"], floor)
        meta = " · ".join(_esc(x) for x in [j["company"], j["location"], j["language"]] if x)
        materials = " &nbsp;·&nbsp; ".join([
            _link(f"{base}/files/{jid}/resume.pdf", "Resume"),
            _link(f"{base}/files/{jid}/cover_letter.pdf", "Cover letter"),
            _link(f"{base}/files/{jid}/fit_summary.md", "Fit summary"),
            _link(f"{base}/#job-{jid}", "Review &amp; rate &rarr;")])
        reason = (f'<div style="font-family:{SERIF};font-style:italic;color:{FG3};'
                  f'font-size:13px;margin:6px 0 0">{_esc(j["relevance_reason"])}</div>'
                  if j["relevance_reason"] else "")
        blocks.append(
            f'<div style="background:{PAPER};border:1px solid {LINE};border-radius:6px;'
            f'padding:14px 16px;margin:0 0 10px;">'
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="font-family:{SANS};font-size:15px;font-weight:600;color:{INK};'
            f'vertical-align:top">{_esc(j["role"])}'
            f'<div style="font-weight:400;font-size:13px;color:{FG3};margin-top:2px">{meta}</div></td>'
            f'<td align="right" style="vertical-align:top;white-space:nowrap">'
            f'{" ".join(badges)} &nbsp;{sal}</td></tr></table>'
            f'{reason}'
            f'<div style="font-size:13px;margin:9px 0 0">{materials}</div>'
            f'</div>')
    return "".join(blocks)


def _section_all(jobs, base) -> str:
    if not jobs:
        return f'<p style="font-family:{SANS};font-size:13px;color:{FG3}">No new jobs tonight.</p>'
    complete = [j for j in jobs if (j["jd_confidence"] or 0) >= 0.5]
    incomplete = [j for j in jobs if (j["jd_confidence"] or 0) < 0.5]

    def row(j) -> str:
        rel = j["relevance_score"]
        title = _esc(j["role"])
        if j["jd_url"]:
            title = (f'<a href="{_esc(j["jd_url"])}" style="color:{INK};'
                     f'text-decoration:none;font-weight:500">{title}</a>')
        hot = (rel or 0) >= config.RELEVANCE_THRESHOLD
        relbadge = (_badge(str(rel), MOSS_SOFT if hot else SUNKEN,
                           MOSS_DK if hot else FG2, MOSS_BORDER if hot else LINE2)
                    if rel is not None else "")
        return (f'<tr>'
                f'<td style="padding:8px 8px 8px 0;border-bottom:1px solid {LINE};'
                f'font-family:{SANS};font-size:13px;color:{INK}">{title}'
                f'<div style="color:{FG3};font-size:12px">{_esc(j["company"])} · '
                f'{_esc(j["source"])}</div></td>'
                f'<td style="padding:8px 0;border-bottom:1px solid {LINE};text-align:right;'
                f'width:46px">{relbadge}</td></tr>')

    out = ['<table width="100%" cellpadding="0" cellspacing="0">']
    out += [row(j) for j in complete]
    out.append("</table>")
    if incomplete:
        out.append(f'<p style="font-family:{SANS};font-size:12px;color:{TERRA_DK};'
                   f'margin:14px 0 4px">JD extraction incomplete — open the link to verify:</p>'
                   '<table width="100%" cellpadding="0" cellspacing="0">')
        out += [row(j) for j in incomplete]
        out.append("</table>")
    out.append(f'<p style="margin:12px 0 0;font-family:{SANS};font-size:13px;color:{FG3}">'
               f'{_link(base + "/", "Rate these on the dashboard &rarr;")}</p>')
    return "".join(out)


def _section_status(conn, base) -> str:
    props = db.pending_proposals(conn)
    if not props:
        return ""
    rows = []
    for p in props:
        rows.append(
            f'<div style="background:{PAPER};border:1px solid {LINE};border-left:2px solid {TERRA};'
            f'border-radius:6px;padding:11px 14px;margin:0 0 8px;font-family:{SANS};font-size:13px">'
            f'<b style="color:{INK}">{_esc(p["proposed_status"]).capitalize()}</b> — '
            f'{_esc(p["role"])} at {_esc(p["company"])}'
            f'<div style="color:{FG3};font-size:12px;margin:3px 0 7px">{_esc(p["evidence"])}</div>'
            f'{_link(base + "/", "Accept or dismiss on the dashboard &rarr;")}</div>')
    return _h2("Possible status changes", "— your call, on the dashboard") + "".join(rows)


def build(conn, *, run_date: Optional[str] = None,
          base_url: Optional[str] = None) -> tuple[str, str]:
    """Return (subject, html) for the digest covering jobs first seen on run_date."""
    run_date = run_date or db.today_str()
    base = (base_url or config.WEB_BASE_URL).rstrip("/")
    floor = _salary_floor()

    jobs = db.jobs_seen_on(conn, run_date)
    # The review queue is the STANDING set of prepared-but-unreviewed jobs, not just
    # tonight's builds — so a quiet sourcing night still re-surfaces older prepared
    # roles instead of showing an empty digest.
    ready = db.reviewable_jobs(conn)
    proposals = db.pending_proposals(conn)

    subject = (f"Job agent — {run_date} — {len(ready)} to review, {len(jobs)} seen"
               + (f", {len(proposals)} status update(s)" if proposals else ""))

    head = (
        f'<div style="background:{PAGE};padding:24px 16px">'
        f'<div style="max-width:680px;margin:0 auto;font-family:{SANS};color:{INK};'
        f'line-height:1.5">'
        f'<div style="border-top:3px solid {MOSS};padding-top:14px">'
        f'<div style="font-family:{MONO};font-size:11px;letter-spacing:.14em;'
        f'text-transform:uppercase;color:{FG3}">Nightly job digest · {run_date}</div>'
        f'<h1 style="font-family:{SERIF};font-weight:400;font-size:26px;letter-spacing:-.01em;'
        f'color:{INK};margin:6px 0 12px;{TNUM}">{len(ready)} ready to review · '
        f'{len(jobs)} roles seen</h1>'
        f'{_btn(base + "/", "Open the review dashboard &rarr;")}</div>')

    body = (
        f'{_section_status(conn, base)}'
        f'{_h2("Ready to review &amp; submit", f"— {len(ready)}")}'
        f'{_section_ready(ready, base, floor)}'
        f'{_h2("Everything seen tonight", "— rate on the dashboard to teach the ranker")}'
        f'{_section_all(jobs, base)}')

    footer = (
        f'<p style="margin:28px 0 0;padding-top:12px;border-top:1px solid {LINE};'
        f'font-family:{MONO};font-size:11px;color:{FG3}">'
        f'{_link(base + "/", "Review dashboard")} · ratings train tomorrow night\'s ranking.'
        f'</p></div></div>')

    return subject, head + body + footer
