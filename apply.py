#!/usr/bin/env python3
"""
Job Application Pipeline
Auto-detects language (DE/EN), produces tailored resume + cover letter as PDF via LaTeX.

Usage:
  python apply.py                            # Interactive: paste JD, Ctrl+D
  python apply.py --jd job.txt               # From file
  python apply.py --jd job.txt --no-photo    # Skip photo (e.g. anonymous applications)
  python apply.py --lang de --jd job.txt     # Force language
  python apply.py --list                     # List past applications
"""

import anthropic
import json
import argparse
import sys
import os
import re
import subprocess
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
import pdfplumber

# ── Config ────────────────────────────────────────────────────────────────────
PROFILE_PATH = Path(__file__).parent / "profile.json"
PHOTO_PATH   = Path(__file__).parent / "photo_cv.jpg"      # cropped + white-bg version for LaTeX
OUTPUT_DIR   = Path(__file__).parent / "applications"
MODEL_STRONG = "claude-opus-4-6"
MODEL_FAST   = "claude-sonnet-4-20250514"

# ── LaTeX escaping ────────────────────────────────────────────────────────────
def latex_escape(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = text.replace('\\', r'\textbackslash{}')
    for old, new in [
        ('&', r'\&'), ('%', r'\%'), ('$', r'\$'), ('#', r'\#'),
        ('_', r'\_'), ('{', r'\{'), ('}', r'\}'),
        ('~', r'\textasciitilde{}'), ('^', r'\textasciicircum{}'),
        ('<', r'\textless{}'),       ('>', r'\textgreater{}'),
        ('|', r'\textbar{}'),        ('"', r"''"),
        ('\u2192', r'$\rightarrow$'), ('\u2013', r'--'),
        ('\u2014', r'---'),           ('\u2026', r'\ldots{}'),
        ('\u20ac', r'\texteuro{}'),
    ]:
        text = text.replace(old, new)
    return text

def esc(bullets):
    if isinstance(bullets, list):
        return [latex_escape(b) for b in bullets]
    return latex_escape(bullets)

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_profile() -> dict:
    with open(PROFILE_PATH) as f:
        return json.load(f)

def read_jd(args) -> str:
    if args.jd:
        return Path(args.jd).read_text(encoding='utf-8')
    print("\n── Paste the job description. Ctrl+D (Mac/Linux) or Ctrl+Z+Enter (Windows) when done ──\n")
    return "".join(sys.stdin.readlines())

def call_claude(system: str, user: str, max_tokens: int = 4000, model: str = MODEL_FAST) -> str:
    client = anthropic.Anthropic()
    retries = 3
    for attempt in range(1, retries + 1):
        try:
            msg = client.messages.create(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}]
            )
            return msg.content[0].text
        except (anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APIConnectionError) as e:
            if attempt == retries:
                raise
            wait = 2 ** attempt
            print(f"   API error ({type(e).__name__}), retrying in {wait}s...")
            time.sleep(wait)

def parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Could not parse JSON:\n{raw[:400]}")

def slugify(text: str) -> str:
    text = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'[\s_-]+', '_', text).strip('_')[:50]

def model_tag(model: str) -> str:
    if "opus" in model:
        return "opus"
    if "sonnet" in model:
        return "sonnet"
    return model.split("-")[1] if "-" in model else model

def step(n, total, label, model: str = None):
    tag = f"  [{model_tag(model)}]" if model else ""
    print(f"[{'█'*n}{'░'*(total-n)}] Step {n}/{total}: {label}{tag}")

# ── Date formatting helpers ───────────────────────────────────────────────────

MONTHS_DE = {
    1: "Jan.", 2: "Feb.", 3: "März", 4: "Apr.", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "Aug.", 9: "Sep.", 10: "Okt.", 11: "Nov.", 12: "Dez."
}
MONTHS_EN = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
}

def format_date(date_str: str, lang: str) -> str:
    """Format a date string like '2025-10' or 'present' for LaTeX display."""
    if not date_str or date_str.lower() == "present":
        return "heute" if lang == "de" else "present"
    parts = date_str.split("-")
    if len(parts) == 2:
        year, month = int(parts[0]), int(parts[1])
        if lang == "de":
            return f"{MONTHS_DE[month]}\\ {year}"
        else:
            return f"{MONTHS_EN[month]} {year}"
    return date_str  # fallback: return as-is


def format_date_range(start: str, end: str, lang: str) -> str:
    """Format a start--end date range for LaTeX."""
    return f"{format_date(start, lang)} -- {format_date(end, lang)}"


def build_language_line(languages: list, lang: str) -> str:
    """Build a LaTeX language line from profile language entries.

    Each entry is {"language": "...", "level": "..."}.
    Returns a LaTeX-escaped string like 'English: Native | German: Fluent'.
    """
    parts = []
    for entry in languages:
        name = entry.get("language", "")
        level = entry.get("level", "")
        # Translate language names to German if needed
        if lang == "de":
            name_map = {"English": "Englisch", "German": "Deutsch", "French": "Französisch",
                        "Spanish": "Spanisch", "Italian": "Italienisch", "Portuguese": "Portugiesisch",
                        "Chinese": "Chinesisch", "Japanese": "Japanisch", "Korean": "Koreanisch",
                        "Russian": "Russisch", "Arabic": "Arabisch", "Dutch": "Niederländisch",
                        "Polish": "Polnisch", "Turkish": "Türkisch", "Swedish": "Schwedisch"}
            name = name_map.get(name, name)
        parts.append(f"{latex_escape(name)}: {latex_escape(level)}")
    return " \\textbar{} ".join(parts)


def translate_degree(degree: str, lang: str) -> str:
    """Translate common degree names to German if lang == 'de'."""
    if lang != "de":
        return degree
    translations = {
        "Bachelor of Arts": "Bachelor of Arts",
        "Bachelor of Science": "Bachelor of Science",
        "Master of Arts": "Master of Arts",
        "Master of Science": "Master of Science",
        "PhD": "Promotion",
        "Doctor of Philosophy": "Promotion",
    }
    return translations.get(degree, degree)


# ── Pipeline steps ────────────────────────────────────────────────────────────

def analyze_jd(jd_text: str, forced_lang: str = None) -> dict:
    step(1, 5, "Analysing job description & detecting language...", MODEL_FAST)
    result = parse_json(call_claude(
        "Expert recruiter. Analyze job descriptions precisely. Output ONLY valid JSON, no markdown.",
        f"""Analyze this job description. Return exactly this JSON:
{{
  "language": "de or en — detected language of the JD",
  "company": "company name",
  "role_title": "exact job title",
  "role_level": "junior or mid or senior or lead",
  "industry": "industry/sector",
  "primary_skills_required": ["must-have skills"],
  "secondary_skills_nice_to_have": ["nice-to-have skills"],
  "key_responsibilities": ["3-5 most important responsibilities as short phrases"],
  "company_values_keywords": ["culture/values words from JD"],
  "ats_keywords": ["exact keywords important for ATS scanning"],
  "role_focus": "one sentence: what does success look like in this role",
  "location": "city / region mentioned in the JD, or 'Remote' if fully remote",
  "strongest_angles": ["2-3 angles to frame this candidate for THIS role"]
}}

Job Description:
{jd_text}""", 1500))
    if forced_lang:
        result['language'] = forced_lang
    lang = result.get('language', 'en')
    print(f"   Language: {'German 🇩🇪' if lang == 'de' else 'English 🇬🇧'} | "
          f"{result.get('role_title')} at {result.get('company')}")
    return result


def check_preferences(profile: dict, jd: dict):
    """Block excluded companies/sectors before any further API calls."""
    prefs = profile.get('job_search', {})
    if not prefs:
        return

    exclusions = prefs.get('exclusions', {})
    company = (jd.get('company') or '').lower()
    industry = (jd.get('industry') or '').lower()

    for exc in exclusions.get('companies', []):
        if exc.lower() == company:
            print(f"\n⚠  Skipping: {jd.get('company')} is in your exclusion list "
                  f"(profile.json → job_search.exclusions.companies)")
            sys.exit(0)

    for exc in exclusions.get('sectors', []):
        if exc.lower() in industry:
            print(f"\n⚠  Skipping: industry \"{jd.get('industry')}\" matches excluded sector "
                  f"\"{exc}\" (profile.json → job_search.exclusions.sectors)")
            sys.exit(0)


def _build_experience_keys_for_prompt(profile: dict) -> str:
    """Build the experience_bullets JSON keys dynamically from profile entries."""
    entries = profile.get('experience', [])
    lines = []
    for entry in entries:
        company = entry.get('company', 'Company')
        is_current = (entry.get('end', '') or '').lower() == 'present'
        if is_current:
            bullet_hint = "2-3 concise bullets, ~20 words each max"
        else:
            bullet_hint = "4-5 concise bullets, ~20 words each max, strongest first"
        lines.append(f'    "{company}": ["{bullet_hint}"]')
    return "{\n" + ",\n".join(lines) + "\n  }"


def match_profile(profile: dict, jd: dict) -> dict:
    step(2, 5, "Matching profile to role...", MODEL_STRONG)
    lang = jd.get('language', 'en')
    lang_note = ("Write ALL text values in formal German (Sie-register)." if lang == 'de'
                 else "Write ALL text values in English.")
    exp_keys = _build_experience_keys_for_prompt(profile)
    return parse_json(call_claude(
        f"Expert resume strategist. {lang_note} Output ONLY valid JSON, no markdown.\n"
        "CRITICAL: Use ONLY facts, numbers, methods, and tools explicitly stated in the PROFILE. "
        "Never invent statistics, metrics, specific numbers, methodologies, or technical details not present in the profile. "
        "If the profile does not mention a specific method or number, do not fabricate one. "
        "If a \"not_yet_implemented\" field exists in an experience entry, never present those items as current capabilities.",
        f"""Select and frame content for a ONE-PAGE resume for this specific role.
{lang_note}

WRITING STYLE — CRITICAL:
- Write the summary in FIRST PERSON (use "ich/mein" in German, "I/my" in English). Never use third person ("er/sie/he/she").
- Each experience bullet should be concise — roughly one printed line (~20 words max). No multi-clause sentences that span 3+ lines. Start with a strong verb or result.
- Aim for easy scanning: a reader should grasp each bullet at a glance.

PROFILE:
{json.dumps(profile, indent=2)}

JD ANALYSIS:
{json.dumps(jd, indent=2)}

Return this JSON (all string values in the output language):
{{
  "headline": "Professional headline, max 12 words, tailored to this role",
  "summary": "2-3 concise sentences in first person, max 50 words total. S1: role + years of experience + core strengths. S2: strongest quantified achievement. S3 (optional): unique value-add. No run-on sentences.",
  "skills_to_feature": {{
    "Category Name": ["skill1", "skill2"],
    "Another Category": ["skill3", "skill4"]
  }},
  "skills_instructions": "Group into 3-5 categories (e.g. Programming, Analytics & Statistics, Data & ETL, Domain, Tools). Max 12 skills total. Match JD keywords and language.",
  "experience_bullets": {exp_keys},
  "education_bullets": ["1-2 short bullets: thesis topic (if relevant to role), focus/specialization, study abroad"],
  "certifications_line": "certification/training line — include if relevant to role, omit if not",
  "framing_notes": "brief internal note on the angle taken for this application"
}}""", 2000, model=MODEL_STRONG))


def generate_cl_content(profile: dict, jd: dict, matched: dict) -> dict:
    lang = jd.get('language', 'en')
    lang_note = ("Write entirely in formal German (Sie-register)." if lang == 'de'
                 else "Write in professional English.")

    # Use most recent experience achievements for cover letter context
    experience_entries = profile.get('experience', [])
    key_achievements = []
    for entry in experience_entries:
        company = entry.get('company', '')
        bullets = matched.get('experience_bullets', {}).get(company, [])
        if bullets:
            key_achievements.extend(bullets[:3])

    return parse_json(call_claude(
        f"Expert cover letter writer. {lang_note} 3 paragraphs only. "
        "P1: why THIS company/role specifically. P2: strongest achievement story with a number. "
        "P3: confident forward close. 220-270 words total. "
        "Never use: 'I am writing to apply', 'passionate about', 'great fit', 'Hiermit bewerbe ich mich'. "
        "CRITICAL: Only reference facts, achievements, methods, and numbers explicitly stated in the profile and matched content. "
        "Never invent or extrapolate specifics (numbers, methodologies, metrics, market counts) not present in the source data. "
        "Output ONLY valid JSON, no markdown.",
        f"""Cover letter for: {jd.get('role_title')} at {jd.get('company')}
Candidate: {profile['personal']['name']}
Framing: {matched.get('framing_notes')}
Role focus: {jd.get('role_focus')}
Strongest angles: {jd.get('strongest_angles')}
Summary to draw from: {matched.get('summary')}
Key achievements: {key_achievements}

Return JSON:
{{
  "salutation": "appropriate salutation for language and culture",
  "paragraph_1": "...",
  "paragraph_2": "...",
  "paragraph_3": "...",
  "closing": "closing phrase ONLY — e.g. 'Best regards,' or 'Mit freundlichen Grüßen' — do NOT include the candidate's name"
}}""", 1200, model=MODEL_STRONG))


def generate_fit_summary(profile: dict, jd: dict, matched: dict) -> dict:
    return parse_json(call_claude(
        "Expert recruiter and compensation analyst. Be honest and analytical. Output ONLY valid JSON, no markdown.",
        f"""Evaluate how well this candidate fits the role, and estimate the salary.

JD ANALYSIS:
{json.dumps(jd, indent=2)}

CANDIDATE PROFILE:
{json.dumps(profile, indent=2)}

MATCHED CONTENT (what was selected for the resume):
{json.dumps(matched, indent=2)}

Return this JSON:
{{
  "fit_percentage": 82,
  "fit_summary": "2-3 sentence overall assessment of candidate-role fit",
  "strengths": ["3-5 strengths relative to this specific role"],
  "gaps": ["1-3 gaps or weaknesses relative to this specific role, be honest"],
  "salary_estimate_eur": {{
    "low": 50000,
    "mid": 58000,
    "high": 65000
  }},
  "salary_notes": "1-2 sentences explaining the estimate: market, location, seniority, industry factors"
}}""", 1500))

# ── LaTeX builders ────────────────────────────────────────────────────────────

def build_resume_tex(profile: dict, jd: dict, matched: dict,
                     photo_path: Path = None) -> str:
    lang = jd.get('language', 'en')
    name    = latex_escape(profile['personal']['name'])
    email   = latex_escape(profile['personal'].get('email', ''))
    phone   = latex_escape(profile['personal'].get('phone', ''))
    address = latex_escape(profile['personal'].get('address', ''))
    city_obj = profile['personal'].get('city', {})
    city    = latex_escape(city_obj.get(lang, city_obj.get('en', '')))

    # Section headers
    if lang == 'de':
        sec_profile, sec_skills, sec_exp, sec_edu = "Profil", "Kenntnisse", "Berufserfahrung", "Ausbildung"
    else:
        sec_profile, sec_skills, sec_exp, sec_edu = "Profile", "Skills", "Experience", "Education"

    # Language line — built dynamically from profile
    languages = profile['personal'].get('languages', [])
    lang_line = build_language_line(languages, lang) if languages else ""

    headline  = esc(matched.get('headline', ''))
    summary   = esc(matched.get('summary', ''))
    raw_skills = matched.get('skills_to_feature', [])
    edu_bullets_raw = matched.get('education_bullets', [])
    if isinstance(edu_bullets_raw, str):
        edu_bullets_raw = [edu_bullets_raw]
    edu_bullets = esc(edu_bullets_raw)
    cert      = matched.get('certifications_line', '')

    if isinstance(raw_skills, dict):
        skills_lines = []
        for category, skill_list in raw_skills.items():
            joined = ", ".join(esc(skill_list) if isinstance(skill_list, list) else [esc(skill_list)])
            skills_lines.append(rf"\textbf{{{esc(category)}:}} {joined}")
        skills_str = " \\\\\n".join(skills_lines)
    else:
        skills_str = ",\\quad ".join(esc(raw_skills))

    def items(bs):
        return "\n    ".join(f"\\item {b}" for b in bs)

    # ── Experience section — dynamic from profile ──
    experience_entries = profile.get('experience', [])
    exp_blocks = []
    for i, entry in enumerate(experience_entries):
        company_name = entry.get('company', '')
        title = entry.get('title', '')
        location = entry.get('location', '')
        start = entry.get('start', '')
        end = entry.get('end', 'present')

        date_range = format_date_range(start, end, lang)
        bullets = esc(matched.get('experience_bullets', {}).get(company_name, []))

        if not bullets:
            continue

        block = rf"""\noindent\textbf{{{esc(title)}}} \hfill \textcolor{{subtle}}{{\textit{{{esc(company_name)}, {esc(location)} \textbar{{}} {date_range}}}}}
\begin{{itemize}}
    {items(bullets)}
\end{{itemize}}"""

        if i > 0:
            block = f"\\vspace{{6pt}}\n\n{block}"

        exp_blocks.append(block)

    experience_tex = "\n\n".join(exp_blocks)

    # ── Education section — dynamic from profile ──
    education_entries = profile.get('education', [])
    edu_blocks = []
    for entry in education_entries:
        degree = entry.get('degree', '')
        major = entry.get('major', '')
        institution = entry.get('institution', '')
        edu_location = entry.get('location', '')
        graduated = entry.get('graduated', '')

        degree_display = translate_degree(degree, lang)
        if major:
            degree_display = f"{degree_display}, {major}"
        if lang == "de":
            degree_display = latex_escape(degree_display.replace("Business", "Betriebswirtschaft")
                                          .replace("Statistics", "Statistik")
                                          .replace("Computer Science", "Informatik")
                                          .replace("Mathematics", "Mathematik")
                                          .replace("Economics", "Volkswirtschaftslehre")
                                          .replace("Physics", "Physik"))
        else:
            degree_display = latex_escape(degree_display)

        institution_display = latex_escape(institution)
        if edu_location:
            institution_display = f"{institution_display}, {latex_escape(edu_location)}"

        # Extract year from graduated string
        year_match = re.search(r'\d{4}', graduated or '')
        year_display = year_match.group() if year_match else graduated or ''

        block = rf"\noindent\textbf{{{degree_display}}} \hfill \textcolor{{subtle}}{{\textit{{{institution_display} \textbar{{}} {year_display}}}}}"
        edu_blocks.append(block)

    edu_section_tex = "\n\n".join(edu_blocks)

    # Education bullet list
    if edu_bullets:
        edu_itemize = f"""\\begin{{itemize}}
    {items(edu_bullets)}
\\end{{itemize}}"""
    else:
        edu_itemize = ""

    # ── Certifications — dynamic from profile ──
    cert_block = ""
    if cert:
        certifications = profile.get('certifications', [])
        if certifications:
            cert_entry = certifications[0]
            cert_name = latex_escape(cert_entry.get('name', cert))
            status = cert_entry.get('status', '')
            if lang == 'de':
                if status == 'in_progress':
                    cert_status = 'In Vorbereitung'
                else:
                    cert_status = status
            else:
                if status == 'in_progress':
                    cert_status = 'In progress'
                else:
                    cert_status = status
            cert_block = rf"""
\vspace{{4pt}}
\noindent\textbf{{{cert_name}}} \hfill \textcolor{{subtle}}{{\textit{{{cert_status}}}}}"""
        else:
            # If no structured certifications, use the matched line directly
            cert_block = rf"""
\vspace{{4pt}}
\noindent\textbf{{{esc(cert)}}}"""

    if photo_path and photo_path.exists():
        photo_file = photo_path.stem
        header = rf"""\begin{{minipage}}[c]{{0.74\textwidth}}
  {{\LARGE\bfseries {name}}}\par\vspace{{4pt}}
  \textcolor{{subtle}}{{{address}, {city}}}\par\vspace{{2pt}}
  \textcolor{{subtle}}{{{email} \textbar{{}} {phone}}}\par\vspace{{2pt}}
  \textcolor{{subtle}}{{{lang_line}}}\par\vspace{{3pt}}
  \textit{{\textcolor{{accent}}{{{headline}}}}}
\end{{minipage}}%
\hfill%
\begin{{minipage}}[c]{{0.22\textwidth}}
  \raggedleft
  \includegraphics[width=\linewidth,keepaspectratio=true]{{{photo_file}}}%
\end{{minipage}}"""
        photo_pkg = r"\usepackage{graphicx}"
    else:
        header = rf"""\begin{{center}}
  {{\LARGE\bfseries {name}}}\\[5pt]
  \textcolor{{subtle}}{{{address}, {city}}}\\[2pt]
  \textcolor{{subtle}}{{{email} \textbar{{}} {phone}}}\\[2pt]
  \textcolor{{subtle}}{{{lang_line}}}
\end{{center}}
\vspace{{2pt}}
\begin{{center}}
  \textit{{\textcolor{{accent}}{{{headline}}}}}
\end{{center}}"""
        photo_pkg = ""

    return rf"""\documentclass[10.5pt,a4paper]{{article}}
\usepackage{{fontspec}}
\usepackage[top=1.8cm, bottom=1.8cm, left=1.8cm, right=1.8cm]{{geometry}}
{photo_pkg}
\usepackage{{titlesec}}
\usepackage{{enumitem}}
\usepackage{{xcolor}}
\usepackage{{parskip}}
\usepackage{{hyperref}}
\usepackage{{microtype}}
\setmainfont{{TeX Gyre Termes}}
\definecolor{{accent}}{{RGB}}{{25, 90, 170}}
\definecolor{{subtle}}{{RGB}}{{90, 90, 90}}
\titleformat{{\section}}{{\large\bfseries\color{{accent}}}}{{}}{{0em}}{{}}[\color{{accent}}\titlerule]
\titlespacing{{\section}}{{0pt}}{{12pt}}{{6pt}}
\setlist[itemize]{{leftmargin=1.4em,itemsep=3pt,topsep=4pt,parsep=0pt,label=\textcolor{{accent}}{{$\bullet$}}}}
\setlength{{\parskip}}{{4pt}}
\setlength{{\parindent}}{{0pt}}

\begin{{document}}
\pagestyle{{empty}}

{header}

\vspace{{4pt}}

\section{{{sec_profile}}}
{summary}

\section{{{sec_skills}}}
{skills_str}

\section{{{sec_exp}}}

{experience_tex}

\section{{{sec_edu}}}
{edu_section_tex}
{edu_itemize}{cert_block}

\end{{document}}
"""


def build_cover_letter_tex(profile: dict, jd: dict, cl: dict) -> str:
    lang    = jd.get('language', 'en')
    name    = latex_escape(profile['personal']['name'])
    address = latex_escape(profile['personal'].get('address', ''))
    city_obj = profile['personal'].get('city', {})
    city    = latex_escape(city_obj.get(lang, city_obj.get('en', '')))
    email   = latex_escape(profile['personal'].get('email', ''))
    phone   = latex_escape(profile['personal'].get('phone', ''))
    company = latex_escape(jd.get('company', ''))
    role    = latex_escape(jd.get('role_title', ''))

    if lang == 'de':
        months_de = ['Januar','Februar','März','April','Mai','Juni',
                     'Juli','August','September','Oktober','November','Dezember']
        d = datetime.now()
        today = f"{d.day}. {months_de[d.month-1]} {d.year}"
        subject_line = f"Bewerbung als {role} -- {company}"
    else:
        today = datetime.now().strftime('%B %d, %Y')
        subject_line = f"Re: {role} -- {company}"

    sal  = latex_escape(cl.get('salutation', 'Dear Hiring Team,'))
    p1   = latex_escape(cl.get('paragraph_1', ''))
    p2   = latex_escape(cl.get('paragraph_2', ''))
    p3   = latex_escape(cl.get('paragraph_3', ''))
    raw_clos = cl.get('closing', 'Best regards,')
    # Strip candidate name if Claude included it in the closing phrase
    raw_name = profile['personal']['name']
    for sep in ['\n', '  ', ',']:
        if raw_name in raw_clos:
            raw_clos = raw_clos.split(raw_name)[0]
    clos = latex_escape(raw_clos.rstrip(' ,\n'))

    return rf"""\documentclass[11pt,a4paper]{{article}}
\usepackage{{fontspec}}
\usepackage[top=2.8cm, bottom=2.5cm, left=2.8cm, right=2.5cm]{{geometry}}
\usepackage{{xcolor}}
\usepackage{{parskip}}
\usepackage{{microtype}}
\setmainfont{{TeX Gyre Termes}}
\definecolor{{accent}}{{RGB}}{{25, 90, 170}}
\setlength{{\parskip}}{{8pt}}
\setlength{{\parindent}}{{0pt}}

\begin{{document}}
\pagestyle{{empty}}

{{\bfseries {name}}}\\
{address}\\
{city}\\
{email} \textbar{{}} {phone}

\vspace{{14pt}}
{today}

\vspace{{14pt}}
{{\bfseries\color{{accent}} {subject_line}}}

\vspace{{6pt}}
{sal}

{p1}

{p2}

{p3}

\vspace{{4pt}}
{clos}\\[22pt]
{name}

\end{{document}}
"""

# ── Page fill measurement ─────────────────────────────────────────────────────

PAGE_H_CM        = 29.7
MARGIN_TOP_CM    = 1.8
MARGIN_BOTTOM_CM = 1.8
USABLE_H_CM      = PAGE_H_CM - MARGIN_TOP_CM - MARGIN_BOTTOM_CM  # 27.1 cm

TARGET_MIN_PCT  = 80
TARGET_MAX_PCT  = 100
MAX_ADJUST_ITER = 3

def measure_pdf(pdf_path: Path) -> dict:
    usable_pts = USABLE_H_CM * 72 / 2.54

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        page1   = pdf.pages[0]
        words1  = page1.extract_words()

        if not words1:
            return {'pages': n_pages, 'fill_pct': 0, 'remaining_cm': USABLE_H_CM,
                    'overflow_cm': 0, 'status': 'expand_major'}

        last_bottom_pts = max(w['bottom'] for w in words1)
        margin_top_pts  = MARGIN_TOP_CM * 72 / 2.54
        content_used    = last_bottom_pts - margin_top_pts
        fill_pct        = content_used / usable_pts * 100

        overflow_cm = 0
        if n_pages > 1:
            page2  = pdf.pages[1]
            words2 = page2.extract_words()
            if words2:
                overflow_cm = max(w['bottom'] for w in words2) * 2.54 / 72

        remaining_cm = (usable_pts - content_used) * 2.54 / 72

        if n_pages > 1 or fill_pct > TARGET_MAX_PCT:
            status = 'overflow'
        elif fill_pct >= TARGET_MIN_PCT:
            status = 'perfect'
        elif fill_pct >= 75:
            status = 'expand_minor'
        else:
            status = 'expand_major'

        return {
            'pages':        n_pages,
            'fill_pct':     round(fill_pct, 1),
            'remaining_cm': round(remaining_cm, 2),
            'overflow_cm':  round(overflow_cm, 2),
            'status':       status,
        }

# ── Expand / trim prompts ─────────────────────────────────────────────────────

def adjust_matched(matched: dict, jd: dict, measurement: dict, iteration: int,
                   profile: dict = None) -> dict:
    lang     = jd.get('language', 'en')
    status   = measurement['status']
    fill_pct = measurement['fill_pct']
    rem_cm   = measurement['remaining_cm']
    over_cm  = measurement['overflow_cm']

    # Build dynamic company references and bullet caps from profile
    experience_entries = (profile or {}).get('experience', [])
    company_names = [e.get('company', '') for e in experience_entries]
    company_list_str = ", ".join(company_names) if company_names else "the listed companies"

    if status == 'overflow':
        instruction = (
            f"The resume currently OVERFLOWS by approximately {over_cm:.1f} cm onto a second page "
            f"(page 1 is {fill_pct:.0f}% full). "
            f"You MUST shorten the content to fit on one page. "
            f"Trim in this order: (1) shorten any verbose bullets to max ~20 words, "
            f"(2) cut the weakest bullet from the longest experience section, "
            f"(3) tighten the summary if needed. "
            f"Do NOT remove any quantified achievements."
        )
    elif status == 'expand_minor':
        instruction = (
            f"The resume is only {fill_pct:.0f}% full with {rem_cm:.1f} cm of blank space remaining. "
            f"Expand to fill the page more completely (target: {TARGET_MIN_PCT}–100%). "
            f"Add 1 NEW bullet to one of the experience sections ({company_list_str}) covering a different relevant achievement. "
            f"Do NOT touch the summary. Do NOT make existing bullets longer — keep each bullet ~20 words max. "
            f"HARD LIMITS: Max 8 bullets per experience entry. "
            f"CRITICAL: Stay strictly within facts stated in the profile. Never invent numbers, methods, metrics, or technical details not explicitly present."
        )
    else:
        instruction = (
            f"The resume is only {fill_pct:.0f}% full with {rem_cm:.1f} cm of blank space remaining. "
            f"Significantly expand the content to fill the page (target: {TARGET_MIN_PCT}–100%). "
            f"Add 1-2 NEW bullets to experience sections ({company_list_str}) covering different relevant achievements. "
            f"Do NOT touch the summary — it must stay as-is. Do NOT make existing bullets longer — keep each bullet ~20 words max. "
            f"HARD LIMITS: Max 8 bullets per experience entry. "
            f"CRITICAL: Stay strictly within facts stated in the profile. Never invent numbers, methods, metrics, or technical details not explicitly present."
        )

    # Build dynamic experience_bullets keys for the JSON template
    exp_keys_lines = []
    for entry in experience_entries:
        company = entry.get('company', 'Company')
        exp_keys_lines.append(f'    "{company}": [...]')
    exp_keys_str = "{\n" + ",\n".join(exp_keys_lines) + "\n  }" if exp_keys_lines else '{"Company": [...]}'

    lang_note = "Keep all text in formal German (Sie-register)." if lang == 'de' \
           else "Keep all text in English."

    system = f"You are a resume editor making precise length adjustments. {lang_note} Output ONLY valid JSON, no markdown."
    user = f"""{instruction}

Current content to adjust:
{json.dumps(matched, indent=2)}

Return the COMPLETE updated JSON with the same structure — all fields, adjusted as instructed:
{{
  "headline": "...",
  "summary": "...",
  "skills_to_feature": [...],
  "experience_bullets": {exp_keys_str},
  "education_bullets": ["..."],
  "certifications_line": "...",
  "framing_notes": "..."
}}"""

    return parse_json(call_claude(system, user, 2500))

# ── LaTeX compiler ────────────────────────────────────────────────────────────

def compile_tex(tex_source: str, output_pdf: Path,
                extra_files: dict = None, label: str = "") -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = Path(tmpdir) / "document.tex"
        tex_path.write_text(tex_source, encoding='utf-8')

        if extra_files:
            for fname, src in extra_files.items():
                shutil.copy(src, Path(tmpdir) / fname)

        result = None
        for _ in range(2):
            result = subprocess.run(
                ['xelatex', '-interaction=nonstopmode', '-halt-on-error',
                 '-output-directory', tmpdir, str(tex_path)],
                capture_output=True, text=True, timeout=60, cwd=tmpdir
            )

        compiled = Path(tmpdir) / "document.pdf"
        if compiled.exists() and result.returncode == 0:
            shutil.copy(compiled, output_pdf)
            return True

        log_path = output_pdf.with_suffix('.compile.log')
        if result:
            log_path.write_text(result.stdout, encoding='utf-8')
            print(f"\n   LaTeX compile error ({label}) → {log_path}")
            for l in [x for x in result.stdout.split('\n') if x.startswith('!')][:5]:
                print(f"   {l}")
        return False

# ── Fit-to-page loop ─────────────────────────────────────────────────────────

def fit_to_page(profile: dict, jd: dict, matched: dict,
                photo_path: Path, out_dir: Path) -> tuple:
    pdf_path = out_dir / "resume.pdf"
    extra    = {photo_path.name: photo_path} if photo_path and photo_path.exists() else {}
    current  = matched

    for iteration in range(1, MAX_ADJUST_ITER + 2):
        tex = build_resume_tex(profile, jd, current, photo_path=photo_path)
        ok  = compile_tex(tex, pdf_path, extra_files=extra, label=f"resume iter {iteration}")
        if not ok:
            return False, current

        m      = measure_pdf(pdf_path)
        pct    = m['fill_pct']
        status = m['status']

        if status == 'perfect':
            print(f"   Iteration {iteration}: {pct:.1f}% full  ✓ perfect — done")
            (out_dir / "resume.tex").write_text(tex, encoding='utf-8')
            return True, current

        rem  = m['remaining_cm']
        over = m['overflow_cm']
        if status == 'overflow':
            print(f"   Iteration {iteration}: {pct:.1f}% full  ✗ overflow ~{over:.1f}cm — trimming...")
        elif status == 'expand_minor':
            print(f"   Iteration {iteration}: {pct:.1f}% full  ↑ {rem:.1f}cm empty — expanding lightly...")
        else:
            print(f"   Iteration {iteration}: {pct:.1f}% full  ↑↑ {rem:.1f}cm empty — expanding significantly...")

        if iteration > MAX_ADJUST_ITER:
            print(f"   Max iterations reached — keeping {pct:.1f}%")
            (out_dir / "resume.tex").write_text(tex, encoding='utf-8')
            return True, current

        current = adjust_matched(current, jd, m, iteration, profile=profile)

    return True, current


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Job Application Pipeline')
    parser.add_argument('--jd',       help='Path to job description text file')
    parser.add_argument('--out',      help='Custom output directory')
    parser.add_argument('--lang',     choices=['en', 'de'], help='Force output language')
    parser.add_argument('--no-photo', action='store_true',  help='Omit photo from resume')
    parser.add_argument('--list',     action='store_true',  help='List past applications')
    args = parser.parse_args()

    if args.list:
        if OUTPUT_DIR.exists():
            apps = sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            print(f"\n{'Application':<58} {'Date'}")
            print("-" * 78)
            for a in apps:
                if a.is_dir():
                    dt = datetime.fromtimestamp(a.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
                    print(f"{a.name:<58} {dt}")
        else:
            print("No applications yet.")
        return

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("Error: ANTHROPIC_API_KEY not set.\nRun: export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)
    if not PROFILE_PATH.exists():
        print(f"Error: profile.json not found at {PROFILE_PATH}")
        sys.exit(1)

    use_photo  = not args.no_photo
    photo_path = PHOTO_PATH if (use_photo and PHOTO_PATH.exists()) else None
    if use_photo and not photo_path:
        print("  Note: photo_cv.jpg not found — generating resume without photo.")

    profile = load_profile()
    jd_text = read_jd(args)
    if not jd_text.strip():
        print("Error: Empty job description."); sys.exit(1)

    print(f"\nStarting pipeline for {profile['personal']['name']}...\n")

    jd      = analyze_jd(jd_text, forced_lang=args.lang)
    check_preferences(profile, jd)
    matched = match_profile(profile, jd)
    step(3, 5, "Generating cover letter content...", MODEL_STRONG)
    cl      = generate_cl_content(profile, jd, matched)
    cl_tex  = build_cover_letter_tex(profile, jd, cl)

    company = jd.get('company', 'company')
    role    = jd.get('role_title', 'role')
    lang    = jd.get('language', 'en')

    if args.out:
        out_dir = Path(args.out)
    else:
        slug    = f"{datetime.now().strftime('%Y%m%d')}_{slugify(company)}_{slugify(role)}_{lang}"
        out_dir = OUTPUT_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "cover_letter.tex").write_text(cl_tex, encoding='utf-8')
    (out_dir / "jd_analysis.json").write_text(json.dumps(jd, indent=2), encoding='utf-8')
    (out_dir / "matched_profile.json").write_text(json.dumps(matched, indent=2), encoding='utf-8')

    step(4, 5, "Compiling & fitting resume to page...", MODEL_FAST)
    resume_ok, final_matched = fit_to_page(profile, jd, matched, photo_path, out_dir)

    (out_dir / "matched_profile_final.json").write_text(
        json.dumps(final_matched, indent=2), encoding='utf-8')

    cl_ok = compile_tex(cl_tex, out_dir / "cover_letter.pdf", label="cover letter")

    step(5, 5, "Generating fit summary & salary estimate...", MODEL_FAST)
    fit = generate_fit_summary(profile, jd, final_matched)
    (out_dir / "fit_summary.json").write_text(json.dumps(fit, indent=2), encoding='utf-8')

    salary = fit.get('salary_estimate_eur', {})

    # Salary preference comparison
    salary_pref = profile.get('job_search', {}).get('salary', {})
    salary_min = salary_pref.get('minimum_eur')
    salary_mid = salary.get('mid')
    salary_line = ""
    if salary_min is not None and salary_mid is not None:
        if salary_mid < salary_min:
            salary_line = f"\n⚠ **Below minimum**: estimated mid (€{salary_mid:,}) is below your minimum (€{salary_min:,})"
        else:
            salary_line = f"\n✓ **Meets salary minimum**: estimated mid (€{salary_mid:,}) ≥ your minimum (€{salary_min:,})"

    # Location preference comparison
    loc_pref = profile.get('job_search', {}).get('location', {})
    jd_location = jd.get('location', '')
    location_line = ""
    if loc_pref and jd_location:
        pref_location = loc_pref.get('preferred', '?')
        remote_ok = loc_pref.get('remote_ok', False)
        location_line = (f"\n**Location**: {jd_location} "
                         f"(Preference: {pref_location}, Remote OK: {'yes' if remote_ok else 'no'})")

    fit_md = f"""# Fit Summary — {role} @ {company}

## Fit: {fit.get('fit_percentage', '?')}%

{fit.get('fit_summary', '')}

### Strengths
{chr(10).join(f'- {s}' for s in fit.get('strengths', []))}

### Gaps
{chr(10).join(f'- {g}' for g in fit.get('gaps', []))}

## Salary Estimate (EUR)

| Low | Mid | High |
|-----|-----|------|
| {salary.get('low', '?'):,} | {salary.get('mid', '?'):,} | {salary.get('high', '?'):,} |

{fit.get('salary_notes', '')}{salary_line}{location_line}
"""
    (out_dir / "fit_summary.md").write_text(fit_md, encoding='utf-8')

    flag       = "🇩🇪 DE" if lang == 'de' else "🇬🇧 EN"
    photo_note = " + photo" if photo_path else " (no photo)"
    final_m    = measure_pdf(out_dir / "resume.pdf") if resume_ok else {}
    fill_note  = f" ({final_m.get('fill_pct', '?')}% full)" if final_m else ""
    fit_pct    = fit.get('fit_percentage', '?')
    salary_mid = salary.get('mid', '?')

    print(f"\n{'='*62}")
    print(f"Done!  [{flag}]  {role} @ {company}")
    print(f"{'='*62}")
    print(f"Output: {out_dir}/\n")
    print(f"  {'✓' if resume_ok else '✗'}  resume.pdf{photo_note}{fill_note}")
    print(f"       └─ resume.tex for manual tweaks")
    print(f"  {'✓' if cl_ok    else '✗'}  cover_letter.pdf")
    print(f"       └─ cover_letter.tex")
    print(f"  ✓  fit_summary.md              ← {fit_pct}% fit | ~€{salary_mid:,} est.")
    if salary_min is not None and salary_mid is not None and isinstance(salary_mid, (int, float)):
        if salary_mid < salary_min:
            print(f"     ⚠  Below minimum: mid €{salary_mid:,} < your €{salary_min:,}")
        else:
            print(f"     ✓  Meets salary minimum (€{salary_min:,})")
    if jd_location and loc_pref:
        pref_loc_display = loc_pref.get('preferred', '?')
        remote_display = 'yes' if loc_pref.get('remote_ok', False) else 'no'
        print(f"     📍 {jd_location} (pref: {pref_loc_display}, remote OK: {remote_display})")
    print(f"     jd_analysis.json           ← verify JD extraction")
    print(f"     matched_profile_final.json ← final content used\n")
    framing = final_matched.get('framing_notes', 'N/A')
    if framing.lower().startswith('angle:'):
        framing = framing[len('angle:'):].strip()
    print(f"  Angle: {framing}\n")

    if not resume_ok or not cl_ok:
        print("  Compile error — edit .tex and rerun:")
        print(f"  cd {out_dir} && xelatex resume.tex\n")


if __name__ == "__main__":
    main()
