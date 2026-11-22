# Job Application Pipeline — instructions for Claude Code

This repo turns a job description into a tailored resume + cover letter (PDF, English or
German), and can run a nightly agent that finds jobs for the user and prepares the best ones
for review. The person you're talking to has probably just cloned it.

## First contact — onboarding comes first
At the start of a conversation, check whether `profile.json` exists in the repo root.

- **No `profile.json`** → greet the user in one or two sentences and offer to run **`/setup`**
  (the guided onboarding in `.claude/skills/setup/SKILL.md`). Don't dump the README on them;
  `/setup` covers the overview and the costs. If they'd rather explore first, answer their
  questions, then offer `/setup` again when they're ready.
- **`profile.json` exists** → run `python3 scripts/doctor.py`. If a required check fails, offer
  to continue `/setup` from that step. Otherwise just help with whatever they ask.

Talk like a helpful friend who knows the tool, not like documentation. Short messages, one
step at a time, confirm before installing anything system-wide (TeX, Homebrew packages) or
anything that costs money.

## How it fits together (read before changing code)
- `apply.py` — the pipeline: analyze the JD → match profile facts → write cover letter →
  build a one-page LaTeX resume (fit-to-page loop) → fit summary. Output goes to
  `applications/<date>_<company>_<role>_<lang>/`. `rebuild_resume.py` re-renders a resume
  from saved JSON without API calls.
- `profile.json` — the user's facts plus `job_search` (location, target roles, salary,
  exclusions). Everything generated comes from here. Schema reference: `profile.example.json`;
  checker: `scripts/check_profile.py`.
- `agent/` + `scripts/nightly_agent.py` — nightly agent: discover jobs (job boards + the
  employer list) and optionally read job-alert emails (Gmail add-on), rank cheaply with Haiku,
  run `apply.py` on the best few, write a digest. `web/app.py` is the review dashboard.
- `scripts/manage.sh` — schedule the agent (launchd on macOS, systemd user units on Linux).
- `scripts/doctor.py` — checks every setup step and prints the fix.
- Docs for people: `README.md`, `docs/HOW_IT_WORKS.md`, `docs/COSTS.md`, `docs/GMAIL.md`,
  `docs/EMPLOYERS.md`.

## Rules
- **Never invent facts about the user.** When drafting or editing `profile.json`, every
  number, employer, date, tool and achievement must come from their documents or their
  answers. If something is unclear or missing, ask — leave it out rather than guess.
- **Keep the anti-hallucination instructions** in `apply.py` prompts. `not_yet_implemented`
  entries must never be presented as current skills.
- **The tool never submits applications.** It prepares documents; the user applies. Don't add
  auto-apply, and don't silently change a job's status — status suggestions are proposals the
  user accepts on the dashboard.
- **No state-changing actions behind GET.** Email clients prefetch links. `/api/rate` and
  `/api/proposal` stay POST-only; the digest only links to dashboard pages.
- **Discovery uses structured feeds only** (documented ATS APIs, sitemaps + JSON-LD). Don't add
  HTML scraping of paths a site's robots.txt disallows.
- **Personal files stay local and untracked:** `profile.json`, `photo_cv.jpg`, `.env`,
  `employers.json`, `applications/`, `data/`, `logs/`. Credentials live in
  `~/.config/job-agent/`. Check `git status` before any commit, and never put the API key
  into a committed file.
- Before telling the user what something costs, use `docs/COSTS.md` or the real usage log
  (`data/usage.jsonl`), not a guess.

## Tooling
- Python 3.10+, virtualenv at `.venv/` (`.venv/bin/python …`).
- XeLaTeX with the TeX Gyre Termes font builds the PDFs.
- `ANTHROPIC_API_KEY` and all settings go in `.env` in the repo root (auto-loaded; real
  environment variables win). Setting names are listed in `docs/HOW_IT_WORKS.md`.
