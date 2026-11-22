# How it works

## One application: `apply.py`

```
.venv/bin/python apply.py --jd path/to/job-ad.txt [--lang en|de] [--no-photo] [--out DIR]
.venv/bin/python apply.py --list          # past applications
```

1. **Analyze the ad.** Role, company, language, must-haves and seniority. Your exclusions
   (`job_search.exclusions`: companies, sectors) are checked here, before any further spend.
2. **Match your profile.** Picks the experience, skills and projects that fit this role, using
   only facts in `profile.json`.
3. **Cover letter**, in the ad's language unless you force one.
4. **Resume.** Built in LaTeX, then tightened until it fits on exactly one page.
5. **Fit summary.** Strengths, gaps, a salary estimate for the role in your market, and what to
   emphasize in an interview.

Output: `applications/<date>_<company>_<role>_<lang>/` with `resume.pdf`, `cover_letter.pdf`,
the JSON behind them, `fit_summary.json` and `usage.json` (tokens + cost).
`rebuild_resume.py applications/<folder>` re-renders the resume from those files with no API
calls, which helps after manual edits.

The prompts tell the model to stay strictly within your profile: no invented numbers, tools
or employers. If a resume says something wrong, the fix belongs in `profile.json`.

## The nightly agent (optional)

`./scripts/manage.sh start` schedules `scripts/nightly_agent.py` (default 05:30) and keeps the
dashboard running. Each night it:

1. **Finds jobs.**
   - Job boards: Arbeitnow (Germany, Austria, Switzerland, no key) and Adzuna (many
     countries, free key).
   - Your employer list (`docs/EMPLOYERS.md`).
   - With the Gmail add-on, your job-alert emails too.
   - Only roles in your city, or remote in your country (per `job_search.location`), are kept.
2. **Dedupes and ranks.** Haiku scores each new job 0–100 against your profile, your
   `job_search` and what you've rated before.
3. **Prepares the best.** Runs `apply.py` on jobs above the threshold, at most
   `JOBAGENT_NIGHTLY_CAP` per night. If your review queue is short, it tops it up with the
   best of the rest.
4. **Status check (Gmail only).** Spots employer replies and *suggests* a status change.
5. **Digest.** Written to `data/digest-latest.html` and shown on the dashboard; emailed to you
   with Gmail on.

It never applies anywhere and never changes a job's status by itself.

**Dashboard** (http://127.0.0.1:8055):
- `/` is the review queue: open the PDFs, rate jobs *Relevant* / *Pass* / *Too senior*, and
  mark the ones you applied to.
- `/jobs` is the history, `/digest` the latest digest.

*Pass* teaches the ranker to show fewer jobs like that one. *Too senior* doesn't: it means
"interesting, just not yet".

## Commands

| Command | What it does |
|---|---|
| `python3 scripts/doctor.py` (`--live`) | Check the setup; `--live` also tests the API key |
| `python3 scripts/check_profile.py` | Validate `profile.json` |
| `./scripts/manage.sh start` / `stop` / `restart` / `status` | Schedule + dashboard on/off |
| `./scripts/manage.sh run-now` (`--dry-run`) | Run the nightly job now (`--dry-run`: find + rank only) |
| `./scripts/manage.sh logs` | Follow the logs in `logs/` |
| `./scripts/manage.sh authorize` | Connect Gmail (add-on) |
| `./scripts/manage.sh uninstall` | Remove the schedule |
| `.venv/bin/python scripts/nightly_agent.py --demo --no-send` | Offline demo with made-up jobs |

macOS uses launchd (`~/Library/LaunchAgents/com.jobagent.*.plist`). Linux uses systemd user
units (`jobagent-nightly.timer`, `jobagent-web.service`); without systemd, `manage.sh` prints a
crontab line instead. On Windows, use WSL (not tested).

## Settings (`.env` in the repo root)

| Setting | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | **Required.** Your API key |
| `JOBAGENT_RUN_AT` | `05:30` | Nightly run time (re-run `manage.sh restart` after changing) |
| `JOBAGENT_WEB_PORT` | `8055` | Dashboard port |
| `JOBAGENT_DISCOVER` | on | `0` turns off job-board/employer discovery |
| `JOBAGENT_DISCOVER_SOURCES` | `auto` | `auto` = Adzuna if a key is set, plus Arbeitnow in DE/AT/CH |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | – | Free Adzuna key (or `~/.config/job-agent/adzuna.json`) |
| `JOBAGENT_GMAIL` | off | `1` turns on the Gmail add-on (`docs/GMAIL.md`) |
| `JOBAGENT_JOB_LABEL` | – | Only read alerts under this Gmail label |
| `JOBAGENT_EXTRA_ALERT_SENDERS` | – | Comma-separated extra alert senders |
| `JOBAGENT_RELEVANCE_THRESHOLD` | `65` | Score needed to build documents |
| `JOBAGENT_NIGHTLY_CAP` | `5` | Max applications built per night |
| `JOBAGENT_MIN_REVIEW` | `3` | Keep at least this many jobs in the review queue |
| `JOBAGENT_NO_PHOTO` | off | `1` builds agent-made resumes without a photo |
| `JOBAPP_MODEL_STRONG` / `JOBAPP_MODEL_FAST` | Opus 5.5 / Sonnet 5.5 | Models (see `docs/COSTS.md`) |
| `JOBAGENT_CREDENTIALS_DIR` | `~/.config/job-agent` | Where Gmail/Adzuna credentials live |

## What's yours (never committed)

`profile.json`, `photo_cv.jpg`, `.env`, `employers.json`, `applications/`, `data/` (job
database, usage log, digest) and `logs/`. Credentials live in `~/.config/job-agent/`.
