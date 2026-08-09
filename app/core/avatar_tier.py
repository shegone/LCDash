"""The ``avatar`` tier: MAE's conversation surface, and nothing else.

Why this exists. The avatar plan (docs/planning/MAE_AVATAR_PLAN_2026-08-09.md)
calls for accounts that can talk to MAE's face -- a demo login at a trade-show
booth, an evaluator invited to try the assistant -- without being able to reach
the dashboard, the map, analytics, or any raw CAD payload. MAE herself answers
from live CAD server-side; the boundary is that this tier's *browser* only ever
receives what MAE chooses to say aloud, never the data feeds those answers are
built from.

Same deny-by-default discipline as the ``user`` tier (app/core/sanitized_tier.py),
for the same reason: the application has well over a hundred routes and grows,
so a new route is invisible to this tier until it is deliberately listed here.

Unlike the ``user`` tier there is no field-sanitizing half: nothing on this
allowlist returns CAD structures at all. If a future route on this list ever
does, it needs the sanitize treatment before being added -- that is the
review gate, and the test suite asserts the raw CAD/analytics APIs stay out.
"""

from __future__ import annotations

from app.core.cloud_pilot_roles import PilotRole

# Exact paths this tier may request.
_AVATAR_ALLOWED_EXACT = frozenset(
    {
        # "/" so a fresh sign-in lands somewhere; the route redirects this
        # tier to /mae/avatar instead of the dashboard.
        "/",
        "/mae/avatar",
        "/logout",
        "/health",
        "/api/identity/whoami",
        # The conversation itself: MAE's cloud advisory chat (text in,
        # spoken-answer text out) and its status probe for the readiness
        # badge. These return MAE's words, not CAD payloads.
        "/api/cloud-ai/status",
        "/api/cloud-ai/advisory",
        "/api/cloud-ai/advisory/stream",
        # Push-to-talk in, audio + viseme timeline out.
        "/api/voice/transcribe",
        "/api/mae/avatar/speech",
    }
)

# Static assets only: the page shell, three.js, the character model, the
# portrait fallback. Nothing under /static/ carries call data.
_AVATAR_ALLOWED_PREFIXES = ("/static/",)


def is_path_allowed_for_avatar(path: str) -> bool:
    """Deny by default. A path is reachable only if it is named above."""

    clean = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if clean in _AVATAR_ALLOWED_EXACT or f"{clean}/" in _AVATAR_ALLOWED_EXACT:
        return True
    return any(clean.startswith(prefix) for prefix in _AVATAR_ALLOWED_PREFIXES)


def restricts(role: str | PilotRole | None) -> bool:
    """True when this role gets the avatar-only tier."""

    return str(role or "") == str(PilotRole.AVATAR)
