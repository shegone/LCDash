"""Which radio paths a signed-in human role may reach through the tier gates.

Mindshare radio intelligence (docs/MINDSHARE_RADIO_CORRECTIONS.md) is open to
every human role: ``user``, ``supervisor``, ``dispatcher``, ``fire_officer`` and
``admin``. The role tier middlewares, though, were written before that
decision: the dispatcher denylist blocks all of ``/mindshare`` and the user and
fire-officer allowlists name nothing under ``/api/radio``. Rather than
scatter a handful of new prefixes through three modules (and make the
"prefix shadows an unreviewed route" sweeps in the tier tests chase them), the
radio paths live here, in one reviewed place, and each tier asks this module.

Deliberately NOT here:

* ``/api/radio/ingest``: machine-signed and identity-blind. No tier ever
  needs to admit a person to it, so it is simply not listed.
* ``/api/radio/replacements`` and ``/api/radio/watch-terms``: admin-only
  management routes. Tiers that deny by default keep them denied (the route
  itself also answers every non-admin with the uniform 403).
* the ``avatar`` role: its allowlist is untouched, so it stays denied.

The patterns are anchored full matches, so nothing outside the listed shapes
(a longer path, an extra segment) becomes reachable by accident.
"""

from __future__ import annotations

import re

RADIO_PAGE = "/mindshare/radio"

_RADIO_EXACT = frozenset(
    {
        RADIO_PAGE,
        "/api/radio/segments",
        "/api/radio/stats",
        "/api/radio/alerts",
    }
)

_RADIO_PATTERNS = (
    # Audio playback and correction/revert of one segment.
    re.compile(r"/api/radio/segments/[^/]+/(?:audio|correction)"),
    # Dismissing one dashboard keyword alert.
    re.compile(r"/api/radio/alerts/[^/]+/dismiss"),
)


def _clean(path: str) -> str:
    return (path or "").split("?", 1)[0].rstrip("/") or "/"


def is_radio_human_path(path: str) -> bool:
    """True for the radio page and APIs that every human role may request."""

    clean = _clean(path)
    if clean in _RADIO_EXACT:
        return True
    return any(pattern.fullmatch(clean) for pattern in _RADIO_PATTERNS)


def is_radio_page(path: str) -> bool:
    """True only for the radio page itself (the dispatcher denylist exception)."""

    return _clean(path) == RADIO_PAGE
