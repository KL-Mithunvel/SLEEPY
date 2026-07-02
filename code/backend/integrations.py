"""
External integrations: Office 365 email.

All functions check for required config before making any network call.
Missing config → log a warning and return without error (opt-in by design).

Public API:
    send_email(to, subject, body_text, *, body_html=None) -> bool
"""

import json
import logging
import urllib.request
import urllib.error

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_json(url: str, *, method: str = "GET", headers: dict | None = None,
               body: dict | None = None, timeout: int = 15) -> dict:
    """Minimal urllib wrapper. Returns parsed JSON dict. Raises on HTTP error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Office 365 email (MSAL + Graph API)
# ---------------------------------------------------------------------------

# Module-level MSAL app cache (one instance per process)
_msal_app = None


def _get_msal_app():
    global _msal_app
    if _msal_app is not None:
        return _msal_app
    try:
        import msal
    except ImportError:
        raise RuntimeError("msal package not installed — run: uv pip install msal")
    if not all([config.O365_CLIENT_ID, config.O365_CLIENT_SECRET, config.O365_TENANT_ID]):
        raise RuntimeError("O365_CLIENT_ID / O365_CLIENT_SECRET / O365_TENANT_ID not configured")
    _msal_app = msal.ConfidentialClientApplication(
        config.O365_CLIENT_ID,
        client_credential=config.O365_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{config.O365_TENANT_ID}",
    )
    return _msal_app


def _get_graph_token() -> str:
    app = _get_msal_app()
    scopes = ["https://graph.microsoft.com/.default"]
    result = app.acquire_token_for_client(scopes=scopes)
    if "access_token" not in result:
        raise RuntimeError(f"MSAL token acquisition failed: {result.get('error_description')}")
    return result["access_token"]


def send_email(to: str, subject: str, body_text: str, *, body_html: str | None = None) -> bool:
    """
    Send an email via Microsoft Graph API using MSAL client credentials.
    Returns True on success, False if not configured or on error.
    """
    if not all([config.O365_CLIENT_ID, config.O365_CLIENT_SECRET, config.O365_TENANT_ID, config.O365_MAILBOX]):
        logger.warning("send_email: O365 credentials not fully configured")
        return False

    try:
        token = _get_graph_token()
        content_type = "HTML" if body_html else "Text"
        body_content = body_html if body_html else body_text
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": content_type, "content": body_content},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": "true",
        }
        mailbox = config.O365_MAILBOX
        url = f"https://graph.microsoft.com/v1.0/users/{mailbox}/sendMail"
        _http_json(url, method="POST", headers={"Authorization": f"Bearer {token}"}, body=payload)
        logger.info("send_email: sent to=%s subject=%r", to, subject)
        return True
    except Exception:
        logger.exception("send_email: failed")
        return False
