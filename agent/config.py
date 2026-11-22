"""Central configuration for the nightly job agent.

All paths and tunables live here so the orchestrator, web app and scripts share
one source of truth. Every knob is an environment variable (set them in the
repo's `.env`, which is gitignored and loaded automatically). Secrets are never
stored here: the Anthropic key comes from ANTHROPIC_API_KEY, and the optional
Gmail/Adzuna credentials live outside the repo in ~/.config/job-agent/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        os.environ.setdefault(key, val.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list_env(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


# ── Paths ──────────────────────────────────────────────────────────────────────
APPLICATIONS_DIR = REPO_ROOT / "applications"               # apply.py output (gitignored)
PROFILE_JSON = REPO_ROOT / "profile.json"                   # candidate source of truth
APPLY_SCRIPT = REPO_ROOT / "apply.py"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

DATA_DIR = REPO_ROOT / "data"
# DB path is env-overridable (handy for tests / a throwaway demo DB)
DB_PATH = Path(os.environ["JOBAGENT_DB"]) if os.environ.get("JOBAGENT_DB") else DATA_DIR / "jobs.db"
JD_TEXTS_DIR = DATA_DIR / "jd_texts"
PREF_PROFILE_PATH = DATA_DIR / "preference_profile.json"
DIGEST_FILE = DATA_DIR / "digest-latest.html"               # always written; emailed only with Gmail on
EMPLOYERS_JSON = REPO_ROOT / "employers.json"                # your list (gitignored)
EMPLOYERS_EXAMPLE_JSON = REPO_ROOT / "agent" / "employers.example.json"

LOG_DIR = REPO_ROOT / "logs"

# Credentials live OUTSIDE the repo (never committed)
CREDENTIALS_DIR = Path(os.environ.get("JOBAGENT_CREDENTIALS_DIR",
                                      str(Path.home() / ".config" / "job-agent"))).expanduser()
GMAIL_CLIENT_SECRET = CREDENTIALS_DIR / "gmail_client_secret.json"
GMAIL_TOKEN = CREDENTIALS_DIR / "gmail_token.json"
ADZUNA_CREDS = CREDENTIALS_DIR / "adzuna.json"

# ── Gmail (optional add-on) ────────────────────────────────────────────────────
# Off unless JOBAGENT_GMAIL=1. When off: no inbox reading, no status tracking,
# and the digest is only written to data/digest-latest.html + shown on the dashboard.
GMAIL_ENABLED = _flag("JOBAGENT_GMAIL", False)

# How job-alert emails are found. No label needed: by default the agent searches
# for mail FROM the usual job-alert senders. If you already file alerts under a
# Gmail label, set JOBAGENT_JOB_LABEL to search only that label instead.
JOB_LABEL = os.environ.get("JOBAGENT_JOB_LABEL", "").strip()
ALERT_SENDERS = _list_env("JOBAGENT_ALERT_SENDERS", ",".join([
    "jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com",
    "alert@indeed.com", "noreply@indeed.com",
    "stepstone.de", "stepstone.at", "xing.com", "glassdoor.com",
    "jobagent.stepstone.de", "arbeitsagentur.de", "monster.com",
    "totaljobs.com", "reed.co.uk", "welcometothejungle.com", "otta.com",
]))
EXTRA_ALERT_SENDERS = _list_env("JOBAGENT_EXTRA_ALERT_SENDERS")  # add your own without replacing the list
PROCESSED_LABEL = "JobAgent/Processed"     # agent-managed; created automatically
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read + add the processed label
    "https://www.googleapis.com/auth/gmail.send",    # send the digest to yourself
]


def alert_query() -> str:
    """Gmail search selecting job-alert mail (label if configured, else by sender)."""
    if JOB_LABEL:
        return f'label:"{JOB_LABEL}"'
    senders = ALERT_SENDERS + EXTRA_ALERT_SENDERS
    return "{" + " ".join(f"from:{s}" for s in senders) + "}"


def digest_to() -> str | None:
    """Digest recipient: JOBAGENT_DIGEST_TO, else the email in profile.json."""
    if os.environ.get("JOBAGENT_DIGEST_TO"):
        return os.environ["JOBAGENT_DIGEST_TO"]
    try:
        return json.loads(PROFILE_JSON.read_text(encoding="utf-8"))["personal"]["email"] or None
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        return None


DIGEST_FROM = "me"                         # Gmail API alias for the authenticated user

# ── Models (cheap calls only; resume generation is apply.py's job) ─────────────
RELEVANCE_MODEL = os.environ.get("JOBAGENT_RELEVANCE_MODEL", "claude-haiku-5-5")
CLASSIFY_MODEL  = os.environ.get("JOBAGENT_CLASSIFY_MODEL",  "claude-haiku-5-5")
SUMMARY_MODEL   = os.environ.get("JOBAGENT_SUMMARY_MODEL",   "claude-sonnet-5-5")

# ── Cost / volume controls ─────────────────────────────────────────────────────
RELEVANCE_THRESHOLD = _int_env("JOBAGENT_RELEVANCE_THRESHOLD", 65)  # min score to build documents
NIGHTLY_CAP = _int_env("JOBAGENT_NIGHTLY_CAP", 5)        # max full-pipeline runs/night (~$0.17 each)
# Floor for the morning review queue: aim to always have >=N jobs WITH prepared docs
# awaiting review. Met first by re-surfacing already-built unreviewed jobs (free), then
# by building the best below-threshold backlog jobs to make up any shortfall. Bounded
# by NIGHTLY_CAP (combined builds/night never exceed it).
MIN_REVIEW_TARGET = _int_env("JOBAGENT_MIN_REVIEW", 3)
BULK_BATCH_SIZE = _int_env("JOBAGENT_BULK_BATCH_SIZE", 15)  # jobs per ranking call
MAX_JOBS_PER_NIGHT = _int_env("JOBAGENT_MAX_JOBS", 60)   # extraction safety valve
INGEST_MAX_AGE_DAYS = _int_env("JOBAGENT_MAX_AGE_DAYS", 7)  # ignore alert emails older than this

# Dashboard "Ready to review" ordering: blend relevance + fit so a high-relevance
# but low-fit role (e.g. a senior post you aren't qualified for) doesn't top the
# list. 1.0 = sort by fit only, 0.0 = relevance only, 0.5 = even split.
RANK_FIT_WEIGHT = _float_env("JOBAGENT_RANK_FIT_WEIGHT", 0.5)

# ── Pipeline ───────────────────────────────────────────────────────────────────
USE_PHOTO = os.environ.get("JOBAGENT_NO_PHOTO") != "1"   # pass --no-photo when False
FORCE_LANG = os.environ.get("JOBAGENT_FORCE_LANG") or None  # None = auto-detect; "en"/"de"

# ── Web (companion review dashboard) ───────────────────────────────────────────
WEB_HOST = "127.0.0.1"
WEB_PORT = _int_env("JOBAGENT_WEB_PORT", 8055)
WEB_BASE_URL = f"http://{WEB_HOST}:{WEB_PORT}"

# ── Status tracking (Gmail only) ───────────────────────────────────────────────
AUTO_ACCEPT_CONFIRMATIONS = False   # always propose, never auto-apply
STATUS_SCAN_DAYS = _int_env("JOBAGENT_STATUS_SCAN_DAYS", 3)

# ── Active sourcing / discovery ────────────────────────────────────────────────
# ON by default: without Gmail this is how jobs come in. JOBAGENT_DISCOVER=0 turns it off.
DISCOVER_ENABLED        = _flag("JOBAGENT_DISCOVER", True)
DISCOVER_MAX_PER_SOURCE = _int_env("JOBAGENT_DISCOVER_PER_SOURCE", 25)
DISCOVER_MAX_TOTAL      = _int_env("JOBAGENT_DISCOVER_MAX", 40)   # new candidates/night ceiling
# Aggregators to query. "auto" = Adzuna (if you added a free key) everywhere it
# operates, plus Arbeitnow for Germany/Austria/Switzerland.
DISCOVER_SOURCES        = _list_env("JOBAGENT_DISCOVER_SOURCES", "auto")
# Optional explicit search-term override; default = job_search.search_terms in profile.json.
DISCOVER_TERMS          = _list_env("JOBAGENT_DISCOVER_TERMS")


def adzuna_credentials() -> tuple[str, str] | None:
    """(app_id, app_key) from env or ~/.config/job-agent/adzuna.json, or None if unset."""
    aid, akey = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if aid and akey:
        return aid, akey
    try:
        d = json.loads(ADZUNA_CREDS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    aid, akey = (d.get("app_id") or ""), (d.get("app_key") or "")
    if aid and akey and "PASTE_" not in aid and "PASTE_" not in akey:
        return aid, akey
    return None
