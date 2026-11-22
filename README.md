# Job Application Pipeline

Paste a job ad, get a **tailored one-page resume and cover letter** as PDFs, in English or
German, written only from facts in your own profile. Optionally, a **nightly agent** searches
job boards and company career pages, ranks what it finds against your profile, prepares
documents for the best matches, and lines them up on a local dashboard for you each morning.

- **Never applies for you.** It prepares documents; you decide and apply.
- **Local.** Your profile, documents and job database stay on your machine. Only the text
  for each AI step goes to Anthropic's API, under your own key.
- **Cheap.** About $0.15–0.20 per application; a daily nightly agent runs roughly
  $8–25/month. Details: [docs/COSTS.md](docs/COSTS.md).
- **Runs on** macOS and Linux (Windows via WSL, untested).

## Get started (recommended)

You need [Claude Code](https://claude.com/claude-code) and an
[Anthropic API key](https://console.anthropic.com/).

```bash
git clone <this repo's URL> job-application-pipeline
cd job-application-pipeline
claude
```

Then type **`/setup`**. Claude explains what the tool does and costs, and builds your
profile from your existing CV, cover letters or LinkedIn export (or asks you questions
instead). It installs and checks each piece with `scripts/doctor.py`, makes your first
application with you, and can turn on the nightly agent if you want it. It takes about
20–30 minutes, mostly waiting for the LaTeX download.

## Manual setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-agent.txt
# LaTeX — macOS: brew install --cask mactex-no-gui · Linux: sudo apt install texlive-xetex texlive-fonts-extra
cp profile.example.json profile.json          # replace Jane Doe with your facts
# create .env containing the line ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/check_profile.py && python3 scripts/doctor.py
.venv/bin/python apply.py --jd job-ad.txt     # → applications/<date>_<company>_<role>_<lang>/
```

Nightly agent: `./scripts/manage.sh run-now --dry-run` to preview, then
`./scripts/manage.sh start`. The dashboard is at http://127.0.0.1:8055.

## Docs

| | |
|---|---|
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | Pipeline steps, nightly agent, commands, all settings |
| [docs/COSTS.md](docs/COSTS.md) | What each step costs and how to cap it |
| [docs/EMPLOYERS.md](docs/EMPLOYERS.md) | Watch specific companies' career pages |
| [docs/GMAIL.md](docs/GMAIL.md) | Optional: read your job-alert emails + email the digest |

## Good to know

- Everything the documents say comes from `profile.json`. If something is wrong or weak, fix
  it there and rerun; the tool won't invent numbers to fill gaps.
- Your files (`profile.json`, `.env`, `applications/`, `data/`, …) are gitignored. Keep it
  that way if you fork.
