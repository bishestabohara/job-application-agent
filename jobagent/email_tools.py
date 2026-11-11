"""Email lookup (Hunter.io, optional) and Gmail: drafts, sending, and reply/bounce detection (headers only)."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import make_msgid
from urllib.parse import urlparse

import requests

from .config import GMAIL_CREDENTIALS, GMAIL_TOKEN, HUNTER_API_KEY, resume_pdf

# compose: drafts · send: sending · metadata: read thread headers only (reply/bounce detection — never message bodies)
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.metadata",
]
MY_ADDRESS = "boharabishesta12@gmail.com"


def find_email(full_name: str, website: str | None) -> tuple[str | None, int | None]:
    """Return (email, confidence 0-100) via Hunter's email finder, or (None, None)."""
    if not (HUNTER_API_KEY and website):
        return None, None
    domain = urlparse(website if "://" in website else f"https://{website}").netloc.removeprefix("www.")
    parts = full_name.split()
    if len(parts) < 2 or not domain:
        return None, None
    r = requests.get("https://api.hunter.io/v2/email-finder", timeout=20, params={
        "domain": domain, "first_name": parts[0], "last_name": parts[-1], "api_key": HUNTER_API_KEY})
    if r.status_code != 200:
        return None, None
    data = r.json().get("data") or {}
    email, score = data.get("email"), data.get("score")
    if not email:
        return None, None
    # second opinion: only keep addresses Hunter's verifier says are deliverable (bounces hurt her Gmail reputation)
    v = requests.get("https://api.hunter.io/v2/email-verifier", timeout=60, params={"email": email, "api_key": HUNTER_API_KEY})
    vd = (v.json().get("data") or {}) if v.status_code == 200 else {}
    if vd.get("result") != "deliverable":
        return None, 0
    return email, max(score or 0, vd.get("score") or 0)


def _gmail():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN, GMAIL_SCOPES) if GMAIL_TOKEN.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not GMAIL_CREDENTIALS.exists():
                raise FileNotFoundError(f"Put your Google OAuth client file at {GMAIL_CREDENTIALS} (see README).")
            creds = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDENTIALS), GMAIL_SCOPES).run_local_server(port=0)
        GMAIL_TOKEN.parent.mkdir(exist_ok=True)
        GMAIL_TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def create_gmail_draft(to: str, subject: str, body: str, resume_version: str | None) -> str:
    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    if resume_version and (pdf := resume_pdf(resume_version)).exists():
        from .apply import RESUME_FILENAMES
        msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=RESUME_FILENAMES[resume_version])
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = _gmail().users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return draft["id"]


def _message(to: str, subject: str, body: str, resume_version: str | None, in_reply_to: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, subject
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    msg.set_content(body)
    if resume_version and (pdf := resume_pdf(resume_version)).exists():
        from .apply import RESUME_FILENAMES
        msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=RESUME_FILENAMES[resume_version])
    return msg


def send_email(to: str, subject: str, body: str, resume_version: str | None = None,
               thread_id: str | None = None, in_reply_to: str | None = None) -> dict:
    """Send now. Follow-ups pass the first email's thread_id + Message-ID so they land in the same conversation."""
    msg = _message(to, subject, body, resume_version, in_reply_to)
    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    sent = _gmail().users().messages().send(userId="me", body=payload).execute()
    return {"id": sent["id"], "thread_id": sent["threadId"], "rfc_message_id": msg["Message-ID"]}


def thread_status(thread_id: str) -> str | None:
    """'replied' if anyone other than Bishesta wrote in the thread, 'bounced' for a delivery failure, else None."""
    t = _gmail().users().threads().get(userId="me", id=thread_id, format="metadata", metadataHeaders=["From", "Subject"]).execute()
    for m in t.get("messages", []):
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "").lower()
        if "mailer-daemon" in sender or "postmaster" in sender or "delivery status" in headers.get("subject", "").lower():
            return "bounced"
        if MY_ADDRESS not in sender:
            return "replied"
    return None


def gmail_ready() -> bool:
    return GMAIL_CREDENTIALS.exists() or GMAIL_TOKEN.exists()
