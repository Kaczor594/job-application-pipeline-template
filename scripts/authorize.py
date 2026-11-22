#!/usr/bin/env python3
"""One-time Gmail OAuth bootstrap.

Opens a browser for consent and writes the token to
~/.credentials/jobagent_token.json (chmod 600). Re-run this if the nightly run
reports that the token has expired.

Prerequisite: a Google Cloud "Desktop app" OAuth client JSON saved at
~/.credentials/jobagent_client_secret.json (see SETUP.md for the Cloud Console steps).

Usage:
  python scripts/authorize.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import config  # noqa: E402


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not config.GMAIL_CLIENT_SECRET.exists():
        print(f"✗ Missing OAuth client secret at:\n    {config.GMAIL_CLIENT_SECRET}\n\n"
              "Create an OAuth 'Desktop app' client in Google Cloud Console and save "
              "the downloaded JSON there. See SETUP.md for the full steps.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.GMAIL_CLIENT_SECRET), config.GMAIL_SCOPES)
    print("Opening a browser for Google consent… (expect an 'unverified app' "
          "warning for a personal Testing app → Advanced → continue)")
    creds = flow.run_local_server(port=0)

    config.GMAIL_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    config.GMAIL_TOKEN.write_text(creds.to_json())
    try:
        config.GMAIL_TOKEN.chmod(0o600)
    except OSError:
        pass
    print(f"✓ Authorized. Token saved to {config.GMAIL_TOKEN}")

    # Sanity check: how many recent job-alert emails can we see?
    from agent.gmail_client import GmailClient
    gc = GmailClient()
    if config.JOB_LABEL and not gc.label_id(config.JOB_LABEL):
        print(f"⚠ Label '{config.JOB_LABEL}' (JOBAGENT_JOB_LABEL) not found in Gmail. "
              "Fix the name in .env, or remove the setting to search by sender instead.")
        return
    n = len(gc.list_unprocessed_job_messages(max_results=500))
    where = f"label '{config.JOB_LABEL}'" if config.JOB_LABEL else "known job-alert senders"
    print(f"✓ Gmail works — {n} job-alert email(s) from the last "
          f"{config.INGEST_MAX_AGE_DAYS} days found ({where}).")
    if n == 0:
        print("  None found. If your alerts come from a site not on the list, add its sender "
              "address to JOBAGENT_EXTRA_ALERT_SENDERS in .env.")


if __name__ == "__main__":
    main()
