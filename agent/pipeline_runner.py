"""Subprocess wrapper around the unchanged apply.py.

Runs one application end-to-end from a saved JD .txt, discovers the new
applications/<slug>/ directory via a before/after set-diff (so apply.py keeps its
own YYYYMMDD_company_role_lang naming), and distinguishes:

  * success  — apply.py exit 0 AND fit_summary.json was written
  * skipped  — exit 0 but no new dir  => check_preferences excluded it
  * error    — non-zero exit / timeout / no fit_summary

Runs are invoked sequentially by the orchestrator (cost cap + ~2 min each), so the
set-diff yields exactly one new directory.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent import config


@dataclass
class PipelineResult:
    ok: bool
    skipped: bool
    error: Optional[str] = None
    app_dir: Optional[Path] = None
    jd_analysis: dict = field(default_factory=dict)
    fit_summary: dict = field(default_factory=dict)


def _snapshot() -> set[str]:
    if not config.APPLICATIONS_DIR.exists():
        return set()
    return {p.name for p in config.APPLICATIONS_DIR.iterdir() if p.is_dir()}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def excluded_by_profile(company: Optional[str], industry: Optional[str] = None) -> bool:
    """Cheap mirror of apply.py:check_preferences so the orchestrator can skip a
    pipeline run for an excluded company/sector entirely (belt-and-suspenders; the
    relevance ranker already scores these ~0)."""
    try:
        prof = json.loads(config.PROFILE_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    exc = (prof.get("job_search", {}) or {}).get("exclusions", {}) or {}
    company_l = (company or "").lower()
    if any((c or "").lower() == company_l for c in exc.get("companies", [])):
        return True
    industry_l = (industry or "").lower()
    return any((s or "").lower() in industry_l for s in exc.get("sectors", []) if s)


def run(jd_path, *, lang: Optional[str] = None, use_photo: Optional[bool] = None,
        timeout: int = 600) -> PipelineResult:
    use_photo = config.USE_PHOTO if use_photo is None else use_photo
    lang = lang or config.FORCE_LANG

    before = _snapshot()
    cmd = [str(config.VENV_PYTHON), str(config.APPLY_SCRIPT), "--jd", str(jd_path)]
    if lang:
        cmd += ["--lang", lang]
    if not use_photo:
        cmd += ["--no-photo"]

    try:
        env = {**os.environ, "JOBAPP_USAGE_SOURCE": "agent-pipeline"}
        proc = subprocess.run(cmd, cwd=str(config.REPO_ROOT), capture_output=True, env=env,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return PipelineResult(False, False, error="apply.py timed out")

    new_dirs = sorted(_snapshot() - before)
    new_dir = config.APPLICATIONS_DIR / new_dirs[-1] if new_dirs else None

    if proc.returncode != 0:
        tail = (proc.stdout[-300:] + proc.stderr[-300:]).strip()
        return PipelineResult(False, False, error=f"apply.py exit {proc.returncode}: {tail}",
                              app_dir=new_dir)

    # exit 0 with no output dir / no fit_summary => check_preferences skipped it
    if new_dir is None or not (new_dir / "fit_summary.json").exists():
        return PipelineResult(False, True, error="excluded by preferences (no output)",
                              app_dir=new_dir)

    return PipelineResult(
        True, False, app_dir=new_dir,
        jd_analysis=_load(new_dir / "jd_analysis.json"),
        fit_summary=_load(new_dir / "fit_summary.json"))
