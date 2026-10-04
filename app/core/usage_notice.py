"""The confidentiality notice every signed-in person must accept, and its gate.

Why this lives in the application. Sign-in is Cognito's hosted page, and the
classic hosted UI this pool uses has no text field at all (only a logo image
and a handful of colour properties), so a notice cannot be placed on that page.
The first page the application controls is the one after sign-in, so the notice
is shown there and must be accepted before any page opens.

What this is and is not. It is a notice-and-acknowledge step plus a
confidentiality line on every page. It is NOT an access control: nothing is
authorised or denied by it, and the cookie that records acceptance is
deliberately unsigned because forging it only skips reading a notice. Real
access control is the ALB and Cognito login and the tier gates in app/core.

Only a human opening a page is stopped. Page navigations always ask for
``text/html``; scripts, API calls and the live event stream do not, so the gate
leaves them alone and a long-running station display is never wedged by it.

The wording is for the county's counsel to approve before it is relied on.
Changing ``NOTICE_VERSION`` makes everyone accept the new wording again.
"""

from __future__ import annotations

from urllib.parse import quote

# Bump when the wording changes materially; everyone is asked again.
NOTICE_VERSION = "2026-10-04"

COOKIE_NAME = "lcdash_notice_ack"
# Matches the ALB session length (24 hours): one acceptance per sign-in.
COOKIE_MAX_AGE_SECONDS = 24 * 60 * 60

NOTICE_PATH = "/notice"

NOTICE_TITLE = "Confidential Information – Authorized Use Only"

NOTICE_PARAGRAPHS = (
    "LCDash contains confidential and sensitive emergency services information, "
    "including information about callers, patients, incidents, locations, and "
    "responders. It is provided only to authorized users, for legitimate public "
    "safety purposes.",
    "All information in this system is private. You must not copy, screenshot, "
    "photograph, record, forward, post, publish, sell, or otherwise disclose, "
    "distribute, or reproduce any of it, in any form or by any means, except as "
    "expressly permitted by Logan County 911 policy and applicable law.",
    "You may use this information only in keeping with the standards and ethics "
    "of emergency services. You must never use it for personal, commercial, "
    "media, or social media purposes, or for any purpose unrelated to your "
    "official duties.",
    "Your access is tied to your account, and activity may be monitored and "
    "logged. Misuse, including unauthorized disclosure, may result in immediate "
    "loss of access, disciplinary action, and civil or criminal liability under "
    "applicable law.",
    "If you received access in error, sign out and notify Logan County 911.",
)

ACCEPT_LABEL = (
    "I have read and understand this notice, and I agree to comply with it."
)

FOOTER_TEXT = (
    "CONFIDENTIAL · Logan County 911 · Authorized use only. "
    "Do not copy, screenshot, or distribute. Activity may be monitored."
)

# Paths the gate never redirects. "/api/" is exempt as a whole because those
# requests are made by scripts, never typed into an address bar; "/static/" is
# the page's own assets, and the notice page needs them to render.
_EXEMPT_EXACT = frozenset({NOTICE_PATH, "/logout", "/health"})
_EXEMPT_PREFIXES = ("/static/", "/api/")


def _normalize(path: str) -> str:
    return (path or "").split("?", 1)[0].rstrip("/") or "/"


def is_gate_exempt(path: str) -> bool:
    clean = _normalize(path)
    return clean in _EXEMPT_EXACT or any(
        clean.startswith(prefix) for prefix in _EXEMPT_PREFIXES
    )


def wants_html_page(method: str, accept: str | None) -> bool:
    """True for a browser page navigation, which is all the gate ever stops."""

    return (method or "").upper() == "GET" and "text/html" in (accept or "").lower()


def has_accepted(cookie_value: str | None) -> bool:
    return (cookie_value or "") == NOTICE_VERSION


def safe_next(value: str | None) -> str:
    """Where to go after accepting; only a local path, never another site.

    Rejects anything that is not a single-slash local path (``//host``,
    ``/\\host``, ``http://...``) and never returns to the notice page itself.
    """

    candidate = (value or "").strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(char) < 32 for char in candidate)
        or _normalize(candidate) == NOTICE_PATH
    ):
        return "/"
    return candidate


def notice_redirect_url(path: str, query: str = "") -> str:
    """The notice page URL that returns the person to the page they asked for."""

    target = path + (f"?{query}" if query else "")
    return f"{NOTICE_PATH}?next={quote(safe_next(target), safe='')}"
