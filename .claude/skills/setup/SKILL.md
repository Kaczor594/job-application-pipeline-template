---
name: setup
description: Guided onboarding for the job application pipeline — explains what it does and costs, builds profile.json from the user's CV / cover letters (or a short Q&A), installs and checks everything with doctor.py, makes a first application, then optionally turns on the nightly agent and the Gmail add-on. Use when the user runs /setup, has no profile.json yet, or wants to resume setup.
---

# /setup — get a friend from clone to first application

Run the steps in order. **One step per message**, short and friendly. After each step say
what's next and wait for the user. If they come back later, run `python3 scripts/doctor.py`
first and pick up at the first failing step instead of starting over.

Ground rules for the whole flow:
- **Never invent facts.** Everything in `profile.json` comes from their documents or their
  answers. Anything unclear → ask. Anything unknown → leave it out. Before writing the file,
  read the draft back as a short summary so they can correct it.
- Ask before installing anything system-wide, and before anything that spends money.
- Their API key never goes through the chat. They put it in `.env` themselves.
- Use `.venv/bin/python` once the virtualenv exists (`python3` before that).

## Step 1 — What this is (≤15 lines, then ask if they want to continue)

Say roughly this, in your own words:

> **What it does.** You give it a job ad; it writes a tailored one-page resume and a cover
> letter (English or German, as PDFs), using only facts from your profile. Optionally a
> nightly agent searches job boards and company career pages for you, ranks what it finds,
> prepares documents for the best few, and shows them on a local dashboard each morning.
>
> **What stays on your machine.** Your profile, the documents, the job database and the
> dashboard are all local. The only thing sent out is the text for each AI step (your
> profile + the job ad) to Anthropic's API, under your own API key. Job boards are only
> queried with search terms. Gmail (optional) is read through your own Google app; nothing
> is shared with me or anyone else.
>
> **What it never does.** It never applies for you, never emails employers, and never
> changes anything without you clicking it on the dashboard.
>
> **Setup takes about 20–30 minutes**, most of it a LaTeX download.

Then ask: "Want me to show what it costs, then we get started?"

## Step 2 — Costs

Show this table (numbers from `docs/COSTS.md`, keep them in sync if that file changes):

| | Cost |
|---|---|
| One application (resume + cover letter) | ≈ $0.15–0.20 |
| Nightly agent, a typical night (ranking + 1–2 applications) | ≈ $0.20–0.40 |
| Nightly agent, maximum night (default cap: 5 applications) | ≈ $0.90 |
| Manual use, ~10 applications a month | ≈ $2/month |
| Nightly agent every day | ≈ $8–25/month |
| Job-board search, Adzuna key, Gmail add-on, dashboard | free |

Mention: you pay Anthropic directly (prepaid credit; a monthly spending limit is in the
Anthropic Console under Limits), every call is logged to `data/usage.jsonl`, and the nightly
cap is the `JOBAGENT_NIGHTLY_CAP` setting.

## Step 3 — Build the profile

Ask: "Do you have an existing CV, cover letters, or a LinkedIn data export I can work from?
Drag the files in or give me their paths. If not, I'll ask you some questions instead."

**From documents:**
- PDF: read it directly. `.docx`: on macOS `textutil -convert txt -stdout <file>`, else
  `.venv/bin/python -c "import zipfile,re,sys;x=zipfile.ZipFile(sys.argv[1]).read('word/document.xml').decode();print(re.sub(r'<[^>]+>',' ',x))" <file>`.
- LinkedIn export (Settings → Data privacy → Get a copy of your data): use `Profile.csv`,
  `Positions.csv`, `Education.csv`, `Skills.csv`, `Certifications.csv`, `Languages.csv`.
- Old cover letters are good for `positioning` and for achievements the CV leaves out.

Draft `profile.json` with the same shape as `profile.example.json`:
- `personal` — name, email, phone, address, `city` as `{"en": "...", "de": "..."}`,
  `languages` with levels.
- `experience[]` — company, location, title, `start`/`end` as `YYYY-MM` (or `"present"`),
  `achievements` (concrete, with their numbers), `tools`.
- `education[]`, `skills` (grouped; each `{name, level, evidence}`), `certifications`,
  `projects` (optional), `positioning` (strongest narrative, differentiators, roles that fit).

Guardrails, every time:
- Every fact must be traceable to a document or an answer. If the CV says "improved
  reporting", don't make up "by 40%" — ask: "Do you have a number for that?"
- Missing month for a date? Ask. Skill level unclear? Ask, or leave the level out.
- Keep a short list of open questions while drafting and ask them together (≤6 at a time).
- Things they're learning but can't do yet go under `not_yet_implemented` in that
  experience entry, never under skills.

**Without documents:** ask in small batches: contact details → each job (title, company,
dates, 3–5 things they did or achieved, tools) → education → skills → languages. Then draft
the same way.

Write the file, show a 5–10 line summary ("2 jobs, 14 skills, 1 degree; open points: …"),
and run `python3 scripts/check_profile.py`. Fix every ✗ with them. Warnings (!) are fine
if they're deliberate.

**Photo:** ask whether photos are normal where they apply (common in Germany, Austria and
Switzerland, usually not elsewhere). If yes, they save a portrait as `photo_cv.jpg` in the
repo root.

## Step 4 — The job search

Ask, and fill `job_search` (schema and examples: `profile.example.json`):
1. Where? City (+ other names for it, e.g. Köln/Cologne), country, 2-letter country code.
2. Remote: only on-site/hybrid in that city (`none`), remote anywhere in the country
   (`country`), or remote from anywhere (`any`)?
3. Which roles? → `target_roles`, plus 2–4 short `search_terms` for the job boards.
4. Words that mark their field (`domain_words`, `domain_phrases`) and role nouns
   (`role_words`), e.g. analyst/engineer. Suggest some from their profile, let them edit.
5. Seniority (`junior`/`mid`/`senior`) and what's too senior → `objective.qualification_fit`.
6. Minimum salary + currency. If they know typical ranges for their target roles, add
   `salary_reference_bands`; otherwise skip them (the tool estimates per job).
7. Languages they'd work in (`en`, `de`, …), work authorization / visa sponsorship, and any
   companies or sectors to exclude.

Run `python3 scripts/check_profile.py` again → must print `OK`.

## Step 5 — Install, each step confirmed by doctor

Run `python3 scripts/doctor.py` and fix the ✗ lines in order. The fix hints it prints are
the commands. Typical order:

1. **Python packages** — `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-agent.txt`
2. **LaTeX** (ask first, it's big):
   - macOS: `brew install --cask mactex-no-gui` (≈5 GB). Smaller: `brew install --cask basictex`,
     then `sudo tlmgr update --self && sudo tlmgr install tex-gyre titlesec enumitem`.
     Open a new terminal afterwards so `xelatex` is on PATH.
   - Linux: `sudo apt install texlive-xetex texlive-fonts-extra` (or the distro's equivalent).
   - Windows: use WSL (Ubuntu) and follow the Linux steps. Not tested on Windows itself.
3. **API key** — they create one at console.anthropic.com → API keys and add credit. Have
   them create `.env` in the repo root themselves with the line
   `ANTHROPIC_API_KEY=sk-ant-...`. Don't ask them to paste the key into the chat.
4. `python3 scripts/doctor.py --live` → one tiny test call (~$0.0001) proves the key works.

Done when doctor ends with **"All required checks pass."** Optional lines (–) are fine.

## Step 6 — First application

Ask them to paste a real job ad (or a link, then fetch the text and show it to confirm).
Save it to `data/first-jd.txt` (`data/` is never committed), then run:

```
.venv/bin/python apply.py --jd data/first-jd.txt
```

Add `--lang en` or `--lang de` to force a language, `--no-photo` to skip the photo. It takes
1–3 minutes. Then show:
- the output folder `applications/<date>_<company>_<role>_<lang>/` and open `resume.pdf` +
  `cover_letter.pdf` for them (`open` on macOS, `xdg-open` on Linux),
- the fit summary and the cost from `usage.json` in that folder.

Ask what they'd change. Wrong or weak facts → fix `profile.json`, not the PDF, and rerun.
Layout-only changes → `.venv/bin/python rebuild_resume.py applications/<folder>` (no API cost).

## Step 7 — Optional: the nightly agent, then Gmail

Ask if they want job hunting on autopilot. If yes:

1. **Employer list** (optional): `cp agent/employers.example.json employers.json`, then
   help them swap in companies they care about. `docs/EMPLOYERS.md` explains how to find a
   company's careers platform.
2. **Job boards:** Arbeitnow works without a key for Germany, Austria and Switzerland.
   Outside those, recommend the free Adzuna key (developer.adzuna.com →
   `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` in `.env`); without it, discovery only checks the
   employer list.
3. **Dry run first (free apart from ranking):**
   `./scripts/manage.sh run-now --dry-run` — shows what it finds and how it ranks it, builds
   nothing. If the matches look wrong, adjust `job_search` (search terms, domain words,
   location) and repeat.
4. **Schedule it:** `./scripts/manage.sh start` (nightly at 05:30 by default; set
   `JOBAGENT_RUN_AT=HH:MM` in `.env` first to change it). Dashboard:
   http://127.0.0.1:8055. Linux: suggest `loginctl enable-linger $USER` so it runs while
   they're logged out. Check with `./scripts/manage.sh status`.
5. **Explain the dashboard:** each morning, rate the prepared jobs (Relevant / Pass / Too
   senior) and mark the ones they applied to. Ratings teach the ranker what they like.
6. **Gmail add-on** (only if they get job-alert emails from LinkedIn, Indeed, StepStone and
   the like): walk through `docs/GMAIL.md` one step at a time, then set `JOBAGENT_GMAIL=1`
   in `.env` and run `./scripts/manage.sh authorize`. No Gmail label is needed; it finds
   alert emails by sender.

Finish with a short recap: what's set up, where things are, and the two commands they'll
use most (`apply.py --jd …` and the dashboard URL). Point to `docs/HOW_IT_WORKS.md` for
everything else.
