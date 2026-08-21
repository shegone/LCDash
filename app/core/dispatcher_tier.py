"""The ``dispatcher`` tier: a supervisor with three doors closed.

Why this exists. Dispatchers need the whole operational product -- live CAD
with call detail, units, station alerts, the map, analytics, reports,
knowledge, and MAE herself. What they do not get (scoped by Ted, 2026-08-20)
is MAE's avatar page, the Mindshare/JACK surface, and the Tools & Quality
section (Integration Health, Voice Lab, MAE Reliability).

Why a DENYLIST when the other two tiers are allowlists. The ``user`` and
``avatar`` tiers exist to withhold data -- PII must not reach those browsers,
so an unreviewed new route defaulting to reachable is the failure. Dispatcher
is the opposite trust shape: the same full-PII access as supervisor, minus
three named product areas. A new operational feature SHOULD appear for
dispatchers the day it ships, exactly as it does for supervisors; hiding
every new route until someone lists it would recreate the maintenance
problem the allowlists were built to avoid, in a tier where the default is
trusted. The denylist below is therefore the review gate in the opposite
direction: extending a BLOCKED area means naming its new path here.

The blocked API surface is deliberately narrow: only endpoints that serve
the blocked pages exclusively. Shared endpoints stay open -- notably
``/api/cloud-ai/advisory*`` and the speech endpoints, which the (allowed)
``/mae`` assistant page uses just as much as the blocked avatar page, and
``/api/mae/feedback``/``/api/mae/memory``, which the chat submits to. The
review-side endpoints (``.../review``, evaluations) belong to MAE
Reliability and are blocked with it.
"""

from __future__ import annotations

from app.core.cloud_pilot_roles import PilotRole

# Exact paths this tier may NOT request.
_DISPATCHER_BLOCKED_EXACT = frozenset(
    {
        # (a) MAE avatar: the page and its viseme-speech endpoint.
        "/mae/avatar",
        "/api/mae/avatar/speech",
        # (c) Tools & Quality pages.
        "/voice",
        "/mae/reliability",
        # Voice Lab's status probe. NOT /api/voice/speech or
        # /api/voice/transcribe -- the MAE assistant page uses those.
        "/api/voice/status",
        # MAE Reliability's quality/review surface.
        "/api/mae/evaluations",
        "/api/mae/evaluations/run",
        "/api/mae/feedback/review",
        "/api/mae/memory/review",
    }
)

# Prefixes this tier may NOT request.
_DISPATCHER_BLOCKED_PREFIXES = (
    # (a) any future avatar-page API.
    "/api/mae/avatar/",
    # (b) Mindshare/JACK: every page and every API.
    "/mindshare",
    "/api/mindshare/",
    # (c) Integration Health page and APIs.
    "/integrations",
    "/api/integrations/",
    # Admin surfaces are already 403'd by _require_pilot_admin; blocking
    # them here too keeps this tier's answer independent of that gate.
    "/admin",
)


def is_path_blocked_for_dispatcher(path: str) -> bool:
    """Allow by default. A path is denied only if it is named above."""

    clean = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if clean in _DISPATCHER_BLOCKED_EXACT or f"{clean}/" in _DISPATCHER_BLOCKED_EXACT:
        return True
    return any(clean.startswith(prefix) for prefix in _DISPATCHER_BLOCKED_PREFIXES)


def restricts(role: str | PilotRole | None) -> bool:
    """True when this role gets the dispatcher tier."""

    return str(role or "") == str(PilotRole.DISPATCHER)
