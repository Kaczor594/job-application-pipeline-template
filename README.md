# Job Application Pipeline

An automated CLI tool that turns a job description into a tailored, one-page resume and cover letter — as print-ready PDFs — in under two minutes.

Built for applying to both German and English-language roles. Language is auto-detected from the job description; resumes follow German conventions (photo, formal register, German section headers) or English conventions automatically.

---

## How It Works

```
Job Description (text)
        │
        ▼
┌───────────────────┐
│  1. JD Analyser   │  Extracts required skills, ATS keywords, role focus,
│   (Claude API)    │  detects language (de/en), identifies strongest angles
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  2. Profile       │  Selects which experiences and skills to feature,
│     Matcher       │  writes tailored headline + summary in correct language
│   (Claude API)    │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  3. Content       │  Generates 3-paragraph cover letter (220–270 words),
│     Generator     │  language-appropriate salutation and closing
│   (Claude API)    │
└────────┬──────────┘
         │
         ▼
┌───────────────────────────────────┐
│  4. Fit-to-Page Loop              │
│                                   │
│  Compile LaTeX → PDF              │
│       │                           │
│       ▼                           │
│  Measure fill % (pdfplumber)      │
│       │                           │
│  < 80%? → Claude expands bullets  │
│  > 100%? → Claude trims bullets   │
│  80–100%? → Done ✓                │
│                                   │
│  Repeats up to 3 iterations       │
└────────┬──────────────────────────┘
         │
         ▼
  resume.pdf  +  cover_letter.pdf
```

---

## Requirements

### System
- Python 3.10+
- XeLaTeX — part of [MacTeX](https://www.tug.org/mactex/) (macOS) or [TeX Live](https://tug.org/texlive/) (Linux/Windows)
- TeX Gyre Termes font (included with both MacTeX and TeX Live)

### Python packages
```bash
pip install anthropic pdfplumber pillow
```

### Anthropic API key
Get one at [console.anthropic.com](https://console.anthropic.com). Cost is approximately $0.10–0.20 per application.

---

## Getting Started

**1. Clone or fork this repo**

**2. Install Python dependencies**
```bash
pip install -r requirements.txt
```

**3. Install XeLaTeX**

macOS:
```bash
brew install --cask mactex-no-gui
```

Ubuntu/Debian:
```bash
sudo apt-get install texlive-xetex texlive-fonts-recommended
```

**4. Set your API key**
```bash
export ANTHROPIC_API_KEY='sk-ant-...'
```

**5. Create your `profile.json`**

Copy the example and fill in your own data:
```bash
cp profile.example.json profile.json
```

Edit `profile.json` with your real information — name, contact details, experience, skills, and education. The example file demonstrates every field the pipeline expects. See [Profile Setup](#profile-setup) below for details.

> `profile.json` is gitignored and will not be committed. Your personal data stays local.

**6. Add your photo** (optional)

Place a headshot as `photo_cv.jpg` in the project root (white background, portrait, face + shoulders, JPEG). This is used for German-style resumes. Use `--no-photo` to skip.

> `photo_cv.jpg` is also gitignored.

---

## Usage

```bash
python apply.py --jd job.txt               # From file
python apply.py                            # Interactive paste (Ctrl+D to finish)
python apply.py --jd job.txt --no-photo    # Skip photo
python apply.py --lang de --jd job.txt     # Force German
python apply.py --list                     # List past applications
```

---

## Output Files

```
applications/
└── 20260302_example_corp_data_analyst_de/
    ├── resume.pdf                  ← Submit this
    ├── cover_letter.pdf            ← Submit this
    ├── resume.tex                  ← Edit and recompile if needed
    ├── cover_letter.tex
    ├── jd_analysis.json            ← Verify JD extraction
    ├── matched_profile.json
    ├── matched_profile_final.json  ← Final content used
    ├── fit_summary.json
    └── fit_summary.md              ← Fit %, salary estimate, strengths/gaps
```

---

## Profile Setup

`profile.json` is the single source of truth for all your candidate data. See `profile.example.json` for the complete structure. Key sections:

| Section | What it contains |
|---|---|
| `personal` | Name, contact info, city (EN/DE), languages, career goals |
| `skills` | Programming, statistics/ML, data/ETL, domain — each with level and evidence |
| `experience` | Companies, titles, dates, achievements, tools used |
| `education` | Institutions, degrees, highlights |
| `certifications` | Optional: certifications in progress or completed |
| `positioning` | Narrative, differentiators, target roles |
| `job_search` | Salary minimum, location preference, company/sector exclusions |

### Job Search Preferences (Active)

The `job_search` section stores your current search criteria. **These preferences are actively enforced by the pipeline:**

- **`exclusions.companies`** — Companies you don't want to apply to. Case-insensitive exact match. **Blocks the pipeline before any further API calls.**
- **`exclusions.sectors`** — Industry sectors to avoid. Case-insensitive substring match against the detected industry.
- **`salary.minimum_eur`** — Your target floor. The fit summary compares the estimated salary against this and flags whether it meets your minimum.
- **`location.preferred`** — City or region you're targeting. Set `remote_ok` to `true` if fully remote roles are acceptable.

If the `job_search` section is missing or empty, the pipeline runs without any preference checks.

---

## Fit-to-Page Logic

| Measured fill | Action |
|---|---|
| > 100% (overflow) | Trim — weakest bullets first, quantified achievements protected |
| 80–100% | Perfect, done |
| 75–80% | Expand — add detail to bullets, lengthen summary |
| < 75% | Expand significantly — add bullets, full 3-sentence summary |

---

## Maintaining Your Profile

`profile.json` is the only file you need to keep updated.

| Event | What to update |
|---|---|
| New job or role change | `experience` — add new entry at the top |
| Complete a notable project | Add to relevant `experience[].achievements` |
| Learn a new tool | `skills` — add with level + evidence |
| Earn a certification | `certifications` section |
| Career goal shifts | `personal.career_goals` |
| Salary / location / company preferences change | `job_search` section |

---

## Troubleshooting

**`xelatex: command not found`** — XeLaTeX not installed or not on PATH. See Setup step 3.

**`ANTHROPIC_API_KEY not set`** — Run `export ANTHROPIC_API_KEY='sk-ant-...'`

**`profile.json not found`** — Copy the example: `cp profile.example.json profile.json` and fill in your data.

**Resume compile error** — Check `.compile.log` in the output folder. Edit `resume.tex` and rerun `xelatex resume.tex`.

**Resume stuck below 80%** — Open `matched_profile_final.json`, add content manually, recompile the `.tex`.
