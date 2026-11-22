"""Build a compact 'what this candidate pursues' context for the relevance ranker.

Sources, in order of strength:
  1. Past applications in applications/ (revealed preference — each was chosen).
     Optional: a brand-new user has none, and the ranker works from (2) alone.
  2. profile.json -> job_search (target roles, seniority, salary floor, location,
     exclusions, objective).
  3. The evolving relevant/pass feedback log from the dashboard (exemplars).

There is NO model training. The "model" is the text produced here, cached to
data/preference_profile.json and re-read by the ranker each night. It is rebuilt
only when applications/ changes or the feedback log grows past a threshold, so the
one summary call is rare.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from agent import config, llm

FEEDBACK_REBUILD_DELTA = 10          # rebuild after this many new ratings
_STOP = {"and", "the", "of", "for", "with", "von", "und", "der", "die", "das",
         "mwd", "wmd", "gn", "in", "im", "ein", "eine", "all", "genders"}


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _title_tokens(title: str) -> list[str]:
    title = re.sub(r"\(.*?\)", " ", title or "")          # drop "(m/w/d)" etc.
    toks = re.findall(r"[a-zA-ZäöüÄÖÜß]+", title.lower())
    return [t for t in toks if len(t) > 2 and t not in _STOP]


def _applications_checksum() -> str:
    if not config.APPLICATIONS_DIR.exists():
        return "0:0"
    dirs = [d for d in config.APPLICATIONS_DIR.iterdir() if d.is_dir()]
    if not dirs:
        return "0:0"
    return f"{len(dirs)}:{int(max(d.stat().st_mtime for d in dirs))}"


def _aggregate_applications() -> dict[str, Any]:
    role_levels: Counter = Counter()
    languages: Counter = Counter()
    title_tokens: Counter = Counter()
    skills: Counter = Counter()
    fits: list[float] = []
    role_lines: list[str] = []          # "Role @ Company — industry — level" for synthesis

    if config.APPLICATIONS_DIR.exists():
        for d in sorted((d for d in config.APPLICATIONS_DIR.iterdir() if d.is_dir())):
            ja = _load_json(d / "jd_analysis.json")
            if not ja or not ja.get("company"):
                continue
            level = str(ja.get("role_level") or "").strip().lower()
            if level:
                role_levels[level] += 1
            if ja.get("language"):
                languages[ja["language"]] += 1
            for tok in _title_tokens(ja.get("role_title", "")):
                title_tokens[tok] += 1
            for sk in (ja.get("primary_skills_required") or []):
                skills[str(sk).strip().lower()] += 1
            fit = _load_json(d / "fit_summary.json") or {}
            if isinstance(fit.get("fit_percentage"), (int, float)):
                fits.append(float(fit["fit_percentage"]))
            role_lines.append(
                f"{ja.get('role_title', '?')} @ {ja['company']}"
                + (f" — {ja['industry']}" if ja.get("industry") else "")
                + (f" — {level}" if level else ""))

    fit_dist = {}
    if fits:
        fit_dist = {
            "n": len(fits),
            "min": round(min(fits)),
            "p25": round(statistics.quantiles(fits, n=4)[0]) if len(fits) >= 4 else round(min(fits)),
            "median": round(statistics.median(fits)),
            "max": round(max(fits)),
        }

    return {
        "n_applications": len(role_lines),
        "role_levels": dict(role_levels.most_common()),
        "languages": dict(languages.most_common()),
        "top_title_tokens": [t for t, _ in title_tokens.most_common(20)],
        "top_skills": [s for s, _ in skills.most_common(25)],
        "fit_distribution": fit_dist,
        "_role_lines": role_lines,
    }


def _hard_constraints() -> tuple[dict[str, Any], dict]:
    prof = _load_json(config.PROFILE_JSON) or {}
    js = prof.get("job_search", {}) or {}
    sal = js.get("salary") or {}
    loc = js.get("location") or {}
    remote = loc.get("remote")
    if remote is None:
        remote = "country" if loc.get("remote_ok") else "none"
    return {
        "target_roles": js.get("target_roles") or
                        (prof.get("positioning", {}) or {}).get("roles_that_fit_well", []),
        "seniority": js.get("seniority"),
        "salary_min": sal.get("minimum", sal.get("minimum_eur")),
        "salary_currency": sal.get("currency") or "EUR",
        "location": loc.get("preferred"),
        "country": loc.get("country"),
        "remote": remote,
        "excluded_companies": (js.get("exclusions") or {}).get("companies", []),
        "excluded_sectors": (js.get("exclusions") or {}).get("sectors", []),
        "objective": js.get("objective") or {},
        "work_authorization": js.get("work_authorization") or {},
    }, prof


def _exemplars(conn) -> tuple[list[str], list[str]]:
    up = [f"{r['role']} @ {r['company']}" for r in conn.execute(
        "SELECT role, company FROM feedback WHERE rating='up' "
        "ORDER BY feedback_id DESC LIMIT 15").fetchall()]
    recent = [f"{r['role']} @ {r['company']}" for r in conn.execute(
        "SELECT role, company FROM jobs WHERE applied=1 "
        "ORDER BY applied_date DESC LIMIT 15").fetchall()]
    # Only genuine-disinterest downvotes feed the "penalize similar" signal.
    # 'qualification' passes (interested but under-qualified) are excluded — they
    # describe a fit gap, not a lack of interest, so they must not suppress the role.
    down = [f"{r['role']} @ {r['company']}" for r in conn.execute(
        "SELECT role, company FROM feedback WHERE rating='down' "
        "AND COALESCE(reason,'interest') != 'qualification' "
        "ORDER BY feedback_id DESC LIMIT 15").fetchall()]
    positive = list(dict.fromkeys(up + recent))[:20]      # dedup, keep order
    return positive, down


def _cold_start_summary(hard: dict, prof: dict) -> str:
    """No application history yet: describe the target from the profile alone (free)."""
    goals = (prof.get("personal", {}) or {}).get("career_goals", {}) or {}
    pos = prof.get("positioning", {}) or {}
    parts = []
    if hard.get("target_roles"):
        parts.append(f"Targets: {', '.join(hard['target_roles'])}.")
    if hard.get("seniority"):
        parts.append(f"Seniority: {hard['seniority']}.")
    if goals.get("short_term"):
        parts.append(f"Short-term goal: {goals['short_term']}.")
    if pos.get("strongest_narrative"):
        parts.append(f"Positioning: {pos['strongest_narrative']}.")
    skills = []
    for group in (prof.get("skills", {}) or {}).values():
        if isinstance(group, list):
            skills += [g.get("name") for g in group if isinstance(g, dict) and g.get("name")]
    if skills:
        parts.append(f"Key skills: {', '.join(skills[:15])}.")
    return " ".join(parts) or "No history yet; score against the stated constraints only."


def _synthesize(agg: dict, hard: dict, prof: dict) -> str:
    """One cheap call: turn past applications into a concrete 'this person
    pursues…' paragraph the ranker can reason against. Free cold-start text when
    there is no history yet."""
    if agg["n_applications"] < 3:
        return _cold_start_summary(hard, prof)
    goals = (prof.get("personal", {}) or {}).get("career_goals", {})
    sample = "\n".join(agg["_role_lines"][-90:])          # most recent ~90 roles
    system = ("You analyze a job-seeker's revealed preferences. Output ONE concise "
              "paragraph (<=180 words), concrete and specific, describing the role "
              "types, industries/domains, seniority, languages, and any clear patterns "
              "in what this person applies to. It will be used to score NEW job postings "
              "for relevance, so emphasize discriminating signals. No preamble.")
    user = (f"Career goals: {json.dumps(goals, ensure_ascii=False)[:600]}\n\n"
            f"Stated constraints: {json.dumps(hard, ensure_ascii=False)}\n\n"
            f"Seniority mix: {agg['role_levels']}  Languages: {agg['languages']}\n"
            f"Most common title words: {agg['top_title_tokens']}\n"
            f"Most common required skills: {agg['top_skills']}\n"
            f"Fit% pursued: {agg['fit_distribution']}\n\n"
            f"Roles applied to (sample):\n{sample}")
    return llm.call(system, user, model=config.SUMMARY_MODEL, max_tokens=400).strip()


def build(conn, *, checksum: str, feedback_count: int) -> dict:
    agg = _aggregate_applications()
    hard, prof = _hard_constraints()
    positive, negative = _exemplars(conn)
    summary = _synthesize(agg, hard, prof)
    agg.pop("_role_lines", None)
    return {
        "built_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "applications_checksum": checksum,
        "feedback_count": feedback_count,
        "summary_text": summary,
        "tallies": agg,
        "hard_constraints": hard,
        "positive_exemplars": positive,
        "negative_exemplars": negative,
    }


def load_or_build(conn, *, force: bool = False) -> dict:
    checksum = _applications_checksum()
    fb_count = conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"]
    if not force and config.PREF_PROFILE_PATH.exists():
        cached = _load_json(config.PREF_PROFILE_PATH)
        if (cached and cached.get("applications_checksum") == checksum
                and fb_count - cached.get("feedback_count", 0) < FEEDBACK_REBUILD_DELTA):
            return cached
    profile = build(conn, checksum=checksum, feedback_count=fb_count)
    config.PREF_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.PREF_PROFILE_PATH.write_text(
        json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return profile


def as_ranking_context(pref: dict) -> str:
    """Compact, token-light context block for the ranker prompt."""
    hard = pref.get("hard_constraints", {})
    remote_txt = {"none": "on-site/hybrid only", "country": "remote within the country OK",
                  "any": "remote anywhere OK"}.get(hard.get("remote"), "")
    where = ", ".join(x for x in (hard.get("location"), hard.get("country")) if x) or "not stated"
    floor = (f"{hard.get('salary_min'):,} {hard.get('salary_currency')}"
             if isinstance(hard.get("salary_min"), (int, float)) else "not stated")
    lines = [
        "WHO THE CANDIDATE IS / WHAT THEY PURSUE:",
        pref.get("summary_text", "").strip(),
        "",
        f"HARD CONSTRAINTS: salary floor {floor}; location {where} ({remote_txt}); "
        f"EXCLUDE companies {hard.get('excluded_companies')}; "
        f"EXCLUDE sectors {hard.get('excluded_sectors')}.",
    ]
    obj = hard.get("objective") or {}
    wa = hard.get("work_authorization") or {}
    if obj or wa:
        lines += ["", "CURRENT OBJECTIVE — weight these HEAVILY, above generic similarity:"]
        if obj.get("summary"):
            lines.append(f"  • Goal: {obj['summary']}")
        if obj.get("qualification_fit"):
            lines.append(f"  • Qualify-now / seniority: {obj['qualification_fit']}")
        if wa:
            if wa.get("needs_sponsorship"):
                lines.append(f"  • Needs visa sponsorship ({wa.get('note', '')}): penalize roles that "
                             "explicitly cannot sponsor or require existing work authorization.")
            else:
                lines.append("  • Work authorization: not an issue — do NOT penalize roles that "
                             "require existing work authorization.")
    if pref.get("positive_exemplars"):
        lines += ["", "RECENTLY PURSUED / UPVOTED (reward similar):",
                  *[f"  + {e}" for e in pref["positive_exemplars"][:15]]]
    if pref.get("negative_exemplars"):
        lines += ["", "DOWNVOTED (penalize similar — score these low):",
                  *[f"  - {e}" for e in pref["negative_exemplars"][:15]]]
    return "\n".join(lines)
