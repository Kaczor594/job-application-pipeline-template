#!/usr/bin/env python3
"""Setup checker — one line per requirement, with the fix for anything missing.

Usage:
  python3 scripts/doctor.py            # quick checks (free, offline)
  python3 scripts/doctor.py --live     # also makes one tiny API call to verify the key (~$0.0001)

Exit code 0 when every REQUIRED check passes (optional add-ons may be off).
Uses only the standard library so it runs before the virtualenv exists.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / "bin" / "python"
OK, BAD, OFF = "✓", "✗", "–"
results: list[tuple[str, bool]] = []          # (symbol, required)


def line(sym: str, label: str, detail: str = "", fix: str = "", required: bool = True) -> None:
    results.append((sym, required))
    print(f"  {sym} {label}" + (f" — {detail}" if detail else ""))
    if fix and sym != OK:
        print(f"      fix: {fix}")


def dotenv() -> dict:
    env = {}
    p = REPO / ".env"
    if p.exists():
        for raw in p.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return {**env, **os.environ}


def tex_path() -> str:
    extra = ["/Library/TeX/texbin"] + [str(p) for p in Path("/usr/local/texlive").glob("*/bin/*")]
    return os.pathsep.join(extra + [os.environ.get("PATH", "")])


def main() -> int:
    live = "--live" in sys.argv
    env = dotenv()
    osname = platform.system()

    print("\nRequired")
    v = sys.version_info
    line(OK if v >= (3, 10) else BAD, "Python 3.10+", f"{v.major}.{v.minor}",
         "install Python 3.10 or newer (macOS: brew install python; Linux: your package manager)")

    if VENV_PY.exists():
        r = subprocess.run([str(VENV_PY), "-c", "import anthropic, pdfplumber, PIL, flask, requests, bs4"],
                           capture_output=True, text=True)
        line(OK if r.returncode == 0 else BAD, "Python packages", "" if r.returncode == 0 else r.stderr.strip().splitlines()[-1],
             ".venv/bin/pip install -r requirements.txt -r requirements-agent.txt")
    else:
        line(BAD, "Python packages", "no .venv yet",
             "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-agent.txt")

    xelatex = shutil.which("xelatex", path=tex_path())
    install_tex = ("macOS: brew install --cask mactex-no-gui (≈5 GB) — or BasicTeX + "
                   "sudo tlmgr install tex-gyre titlesec enumitem; "
                   "Linux: sudo apt install texlive-xetex texlive-fonts-extra")
    line(OK if xelatex else BAD, "XeLaTeX (PDF compiler)", xelatex or "not found", install_tex)
    if xelatex:
        kpse = shutil.which("kpsewhich", path=tex_path())
        font = subprocess.run([kpse, "texgyretermes-regular.otf"], capture_output=True, text=True).stdout.strip() if kpse else ""
        line(OK if font else BAD, "TeX Gyre Termes font", "found" if font else "missing",
             "macOS BasicTeX: sudo tlmgr install tex-gyre; Linux: sudo apt install texlive-fonts-extra")

    key = env.get("ANTHROPIC_API_KEY", "")
    in_dotenv = "ANTHROPIC_API_KEY=" in ((REPO / ".env").read_text(encoding="utf-8")
                                         if (REPO / ".env").exists() else "")
    where = "in .env" if in_dotenv else "shell only — add it to .env so the nightly run sees it"
    line(OK if key.startswith("sk-ant-") else BAD, "Anthropic API key",
         f"set ({where})" if key else "missing",
         "create one at console.anthropic.com → API keys, then put ANTHROPIC_API_KEY=sk-ant-... in .env")
    if live and key and VENV_PY.exists():
        code = ("import anthropic;c=anthropic.Anthropic();"
                "m=c.messages.create(model='claude-haiku-5-5',max_tokens=50,"
                "messages=[{'role':'user','content':'Reply OK'}]);print('ok')")
        r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True,
                           env={**os.environ, "ANTHROPIC_API_KEY": key})
        msg = r.stderr.strip().splitlines()[-1] if r.returncode else "API call succeeded"
        line(OK if r.returncode == 0 else BAD, "API key works", msg[:160],
             "check the key, and that the account has credit (console.anthropic.com → Billing)")

    prof_path = REPO / "profile.json"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "check_profile.py"), str(prof_path)],
                       capture_output=True, text=True)
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?"
    line(OK if r.returncode == 0 else BAD, "profile.json", last,
         "run /setup in Claude Code to build it from your CV, or: python3 scripts/check_profile.py")
    if r.returncode != 0:
        for ln in r.stdout.strip().splitlines()[:-1][:6]:
            print(f"      {ln.strip()}")

    print("\nOptional")
    photo = REPO / "photo_cv.jpg"
    line(OK if photo.exists() else OFF, "CV photo (photo_cv.jpg)", "found" if photo.exists() else "none — resumes have no photo",
         "only add one if photos are customary where you apply (e.g. Germany, Austria, Switzerland)", required=False)

    emp = REPO / "employers.json"
    if emp.exists():
        try:
            n = len(json.loads(emp.read_text(encoding="utf-8")))
            line(OK, "Employer watch-list", f"{n} employers in employers.json", required=False)
        except json.JSONDecodeError as e:
            line(BAD, "Employer watch-list", f"employers.json is not valid JSON (line {e.lineno})",
                 "fix the JSON syntax", required=False)
    else:
        line(OFF, "Employer watch-list", "using agent/employers.example.json",
             "cp agent/employers.example.json employers.json and edit (see docs/EMPLOYERS.md)", required=False)

    cred_dir = Path(env.get("JOBAGENT_CREDENTIALS_DIR", str(Path.home() / ".config" / "job-agent"))).expanduser()
    adz = (env.get("ADZUNA_APP_ID") and env.get("ADZUNA_APP_KEY")) or (cred_dir / "adzuna.json").exists()
    line(OK if adz else OFF, "Adzuna job-board key (free)", "set" if adz else "not set — Adzuna skipped",
         "free key at developer.adzuna.com → put ADZUNA_APP_ID / ADZUNA_APP_KEY in .env", required=False)

    gmail_on = env.get("JOBAGENT_GMAIL", "").lower() in ("1", "true", "yes", "on")
    if not gmail_on:
        line(OFF, "Gmail add-on", "off (jobs come from discovery only)",
             "see docs/GMAIL.md, then set JOBAGENT_GMAIL=1 in .env", required=False)
    else:
        secret = (cred_dir / "gmail_client_secret.json").exists()
        token = (cred_dir / "gmail_token.json").exists()
        line(OK if secret else BAD, "Gmail OAuth client", "found" if secret else f"missing in {cred_dir}",
             "docs/GMAIL.md steps 1–4", required=False)
        line(OK if token else BAD, "Gmail authorized", "token found" if token else "not yet",
             "./scripts/manage.sh authorize", required=False)
        problem = REPO / "logs" / "AUTH_PROBLEM.txt"
        if problem.exists():
            line(BAD, "Gmail token health", "last run could not use Gmail",
                 "./scripts/manage.sh authorize  (then delete logs/AUTH_PROBLEM.txt)", required=False)

    port = int(env.get("JOBAGENT_WEB_PORT", "8055") or 8055)
    with socket.socket() as s:
        s.settimeout(0.5)
        up = s.connect_ex(("127.0.0.1", port)) == 0
    if osname == "Darwin":
        installed = (Path.home() / "Library" / "LaunchAgents" / "com.jobagent.nightly.plist").exists()
    elif osname == "Linux":
        installed = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
                     / "systemd" / "user" / "jobagent-nightly.timer").exists()
    else:
        installed = False
    line(OK if installed else OFF, "Nightly schedule", "installed" if installed else "not installed",
         "./scripts/manage.sh start", required=False)
    line(OK if up else OFF, "Dashboard", f"http://127.0.0.1:{port}" if up else "not running",
         "./scripts/manage.sh start  (or run once: .venv/bin/python web/app.py)", required=False)

    failed = [s for s, req in results if req and s == BAD]
    print("\n" + ("All required checks pass." if not failed
                  else f"{len(failed)} required check(s) need attention."))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
