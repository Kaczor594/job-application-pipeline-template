"""Gmail API client: OAuth token load/refresh, find job-alert mail, parse
message bodies, label processed mail, search the inbox, and send the HTML digest.

Auth is set up once via scripts/authorize.py (browser consent). The nightly run is
headless and cannot open a browser, so credential loading raises a clear,
actionable GmailAuthError if the token is missing or unrefreshable — the
orchestrator surfaces that loudly instead of failing silently.

Google libraries are imported lazily so this module imports cleanly even before
the deps/token exist.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Optional

from agent import config


class GmailAuthError(RuntimeError):
    """Raised when the Gmail token is missing/expired and needs re-authorization."""


@dataclass
class Message:
    id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    text: str            # decoded text/plain body
    html: str            # decoded text/html body
    links: list[str]     # hrefs pulled from the html

    @property
    def body(self) -> str:
        """Best available body text (plain preferred, else stripped html)."""
        if self.text.strip():
            return self.text
        return re.sub(r"<[^>]+>", " ", self.html)


# ── credentials ─────────────────────────────────────────────────────────────────
def load_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not config.GMAIL_TOKEN.exists():
        raise GmailAuthError(
            f"No Gmail token at {config.GMAIL_TOKEN}.\n"
            f"Run:  ./scripts/manage.sh authorize")
    creds = Credentials.from_authorized_user_file(
        str(config.GMAIL_TOKEN), config.GMAIL_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                config.GMAIL_TOKEN.write_text(creds.to_json())
            except Exception as e:  # noqa: BLE001 — surface any refresh failure clearly
                raise GmailAuthError(
                    f"Gmail token refresh failed ({e}). Re-run scripts/authorize.py."
                ) from e
        else:
            raise GmailAuthError(
                "Gmail token is invalid and not refreshable. Re-run scripts/authorize.py.")
    return creds


def token_healthy() -> tuple[bool, str]:
    """Non-raising check used by the nightly run to warn before doing real work."""
    try:
        load_credentials()
        return True, "ok"
    except GmailAuthError as e:
        return False, str(e)


# ── client ──────────────────────────────────────────────────────────────────────
class GmailClient:
    def __init__(self):
        from googleapiclient.discovery import build
        self.svc = build("gmail", "v1", credentials=load_credentials(),
                         cache_discovery=False)
        self._label_cache: dict[str, str] = {}

    # labels -----------------------------------------------------------------
    def label_id(self, name: str, create: bool = False) -> Optional[str]:
        if name in self._label_cache:
            return self._label_cache[name]
        labels = self.svc.users().labels().list(userId="me").execute().get("labels", [])
        for lab in labels:
            if lab["name"].lower() == name.lower():
                self._label_cache[name] = lab["id"]
                return lab["id"]
        if create:
            created = self.svc.users().labels().create(
                userId="me",
                body={"name": name, "labelListVisibility": "labelShow",
                      "messageListVisibility": "show"}).execute()
            self._label_cache[name] = created["id"]
            return created["id"]
        return None

    def ensure_processed_label(self) -> str:
        return self.label_id(config.PROCESSED_LABEL, create=True)

    # read -------------------------------------------------------------------
    def _list_ids(self, query: str, max_results: int) -> list[str]:
        ids: list[str] = []
        token = None
        while len(ids) < max_results:
            resp = self.svc.users().messages().list(
                userId="me", q=query, pageToken=token,
                maxResults=min(100, max_results - len(ids))).execute()
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                break
        return ids[:max_results]

    def list_unprocessed_job_messages(self, max_results: int = 200) -> list[str]:
        q = (f'{config.alert_query()} -label:"{config.PROCESSED_LABEL}" '
             f'newer_than:{config.INGEST_MAX_AGE_DAYS}d')
        return self._list_ids(q, max_results)

    def search(self, query: str, max_results: int = 100) -> list[str]:
        return self._list_ids(query, max_results)

    def get_message(self, msg_id: str) -> Message:
        m = self.svc.users().messages().get(
            userId="me", id=msg_id, format="full").execute()
        payload = m.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        text, html = _extract_bodies(payload)
        return Message(
            id=m["id"], thread_id=m.get("threadId", ""),
            sender=headers.get("from", ""), subject=headers.get("subject", ""),
            date=headers.get("date", ""), text=text, html=html,
            links=_extract_links(html))

    # write ------------------------------------------------------------------
    def add_processed_label(self, msg_id: str) -> None:
        lid = self.ensure_processed_label()
        self.svc.users().messages().modify(
            userId="me", id=msg_id, body={"addLabelIds": [lid]}).execute()

    def send_html(self, to: str, subject: str, html: str) -> dict:
        mime = MIMEText(html, "html", "utf-8")
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        return self.svc.users().messages().send(
            userId="me", body={"raw": raw}).execute()


# ── MIME helpers ────────────────────────────────────────────────────────────────
def _decode(data: str) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def _extract_bodies(payload: dict) -> tuple[str, str]:
    """Depth-first walk of MIME parts → (plain_text, html)."""
    text_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if mime == "text/plain" and body.get("data"):
            text_parts.append(_decode(body["data"]))
        elif mime == "text/html" and body.get("data"):
            html_parts.append(_decode(body["data"]))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(text_parts), "\n".join(html_parts)


def _extract_links(html: str) -> list[str]:
    if not html:
        return []
    seen, out = set(), []
    for href in re.findall(r'href=["\'](https?://[^"\']+)["\']', html, re.I):
        if href not in seen:
            seen.add(href)
            out.append(href)
    return out
