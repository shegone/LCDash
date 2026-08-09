"""Trusted per-user identity recovered from ALB-signed OIDC request headers.

The pilot authenticates every request at the load balancer with an
``authenticate-cognito`` action, so the application never runs a login flow of
its own. The ALB then forwards the authenticated user to the container in three
headers. Recovering identity from them requires two *separate* verifications,
because neither header alone is both trustworthy and complete:

``x-amzn-oidc-data``
    User claims from the IdP *userInfo* endpoint, signed by the load balancer
    with ES256. Verifying it -- and asserting that the ``signer`` field names
    our own load balancer -- is what proves a request actually arrived through
    the ALB rather than being forged by anything else that can reach the
    container. It does NOT carry Cognito group membership: the ALB explicitly
    does not forward ID-token claims, and the Cognito userInfo endpoint omits
    ``cognito:groups``.

``x-amzn-oidc-accesstoken``
    Cognito's own access token, signed with RS256 against the user pool's
    JWKS. This is the only place ``cognito:groups`` is available, so it is the
    source of role membership.

Both are verified. The ALB assertion establishes provenance; the Cognito token
establishes authorization. Every failure path returns ``None`` -- this module
never falls back to a default role, because a silent fallback here would grant
access on a verification bug.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key

logger = logging.getLogger(__name__)

OIDC_DATA_HEADER = "x-amzn-oidc-data"
OIDC_ACCESS_TOKEN_HEADER = "x-amzn-oidc-accesstoken"
OIDC_IDENTITY_HEADER = "x-amzn-oidc-identity"

_ALB_KEY_ENDPOINT = "https://public-keys.auth.elb.{region}.amazonaws.com/{key_id}"
_COGNITO_JWKS_URL = (
    "https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
)
_COGNITO_ISSUER = "https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

# The ALB rotates signing keys, so the key set is unbounded over time. Cap the
# cache so a rotation storm cannot grow it without limit.
_ALB_KEY_CACHE_MAX = 16
_KEY_FETCH_TIMEOUT_SECONDS = 5.0


class AlbIdentityError(Exception):
    """Internal signal that a header failed verification; never surfaced raw."""


@dataclass(frozen=True, slots=True)
class AlbIdentity:
    """A verified end user, as asserted by the load balancer and Cognito."""

    subject: str
    groups: tuple[str, ...]
    email: str | None = None


class _AlbPublicKeys:
    """Bounded, thread-safe cache of the ALB's ES256 signing keys."""

    def __init__(self, fetch: Callable[[str, str], str] | None = None) -> None:
        self._fetch = fetch or _http_get_text
        self._keys: dict[str, object] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def get(self, *, region: str, key_id: str):
        with self._lock:
            cached = self._keys.get(key_id)
        if cached is not None:
            return cached

        url = _ALB_KEY_ENDPOINT.format(region=region, key_id=key_id)
        pem = self._fetch(url, "alb-public-key")
        try:
            key = load_pem_public_key(pem.strip().encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - opaque crypto failures
            raise AlbIdentityError("ALB public key was not usable.") from exc

        with self._lock:
            self._keys[key_id] = key
            self._order.append(key_id)
            while len(self._order) > _ALB_KEY_CACHE_MAX:
                self._keys.pop(self._order.pop(0), None)
        return key


def _http_get_text(url: str, purpose: str) -> str:
    try:
        response = httpx.get(url, timeout=_KEY_FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - network/HTTP detail is not actionable here
        raise AlbIdentityError(f"Could not retrieve {purpose}.") from exc
    return response.text


_alb_keys = _AlbPublicKeys()
_cognito_clients: dict[str, jwt.PyJWKClient] = {}
_cognito_clients_lock = threading.Lock()


def _cognito_jwk_client(*, region: str, user_pool_id: str) -> jwt.PyJWKClient:
    """One cached JWKS client per pool; PyJWKClient caches keys internally."""

    url = _COGNITO_JWKS_URL.format(region=region, user_pool_id=user_pool_id)
    with _cognito_clients_lock:
        client = _cognito_clients.get(url)
        if client is None:
            client = jwt.PyJWKClient(
                url,
                cache_keys=True,
                timeout=int(_KEY_FETCH_TIMEOUT_SECONDS),
            )
            _cognito_clients[url] = client
        return client


def _verify_alb_assertion(
    token: str,
    *,
    region: str,
    expected_alb_arn: str,
    keys: _AlbPublicKeys,
) -> Mapping[str, object]:
    """Verify the load balancer's own signature over the userInfo claims."""

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        raise AlbIdentityError("ALB assertion header was unreadable.") from exc

    if header.get("alg") != "ES256":
        raise AlbIdentityError("ALB assertion used an unexpected algorithm.")

    # The signer field is the load balancer's ARN. Checking it is what stops a
    # token minted by *some other* load balancer from being accepted here.
    signer = str(header.get("signer") or "")
    if not expected_alb_arn or signer != expected_alb_arn:
        raise AlbIdentityError("ALB assertion was not signed by this load balancer.")

    # The ALB puts expiry in the JWT *header*, which PyJWT does not police --
    # it only validates payload claims. So check it explicitly.
    expires_at = header.get("exp")
    if expires_at is not None:
        try:
            if float(expires_at) <= time.time():
                raise AlbIdentityError("ALB assertion has expired.")
        except (TypeError, ValueError) as exc:
            raise AlbIdentityError("ALB assertion expiry was unreadable.") from exc

    key_id = str(header.get("kid") or "")
    if not key_id:
        raise AlbIdentityError("ALB assertion did not name a signing key.")

    key = keys.get(region=region, key_id=key_id)
    try:
        # The payload holds arbitrary userInfo claims rather than standard
        # registered claims, so there is no audience or issuer to check here.
        # Provenance is established by the signature and the signer check above.
        return jwt.decode(
            token,
            key=key,
            algorithms=["ES256"],
            options={"verify_aud": False, "verify_iss": False, "verify_exp": False},
        )
    except Exception as exc:  # noqa: BLE001
        raise AlbIdentityError("ALB assertion signature was invalid.") from exc


def _verify_cognito_access_token(
    token: str,
    *,
    region: str,
    user_pool_id: str,
    expected_client_id: str | None,
) -> Mapping[str, object]:
    """Verify Cognito's access token, the only carrier of group membership."""

    client = _cognito_jwk_client(region=region, user_pool_id=user_pool_id)
    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except Exception as exc:  # noqa: BLE001
        raise AlbIdentityError("Cognito signing key was unavailable.") from exc

    try:
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            issuer=_COGNITO_ISSUER.format(region=region, user_pool_id=user_pool_id),
            # Cognito access tokens carry client_id rather than aud.
            options={"verify_aud": False, "require": ["exp", "iss"]},
        )
    except Exception as exc:  # noqa: BLE001
        raise AlbIdentityError("Cognito access token was invalid.") from exc

    if str(claims.get("token_use") or "") != "access":
        raise AlbIdentityError("Cognito token was not an access token.")

    if expected_client_id and str(claims.get("client_id") or "") != expected_client_id:
        raise AlbIdentityError("Cognito access token was issued to another client.")

    return claims


def _extract_groups(claims: Mapping[str, object]) -> tuple[str, ...]:
    raw = claims.get("cognito:groups")
    if raw is None:
        return ()
    if isinstance(raw, str):
        # Cognito sends a JSON array, but tolerate a space/comma delimited
        # string rather than silently dropping every group.
        parts = [part.strip() for part in raw.replace(",", " ").split()]
        return tuple(part for part in parts if part)
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def resolve_alb_identity(
    headers: Mapping[str, str],
    *,
    region: str,
    expected_alb_arn: str,
    user_pool_id: str,
    expected_client_id: str | None = None,
    keys: _AlbPublicKeys | None = None,
) -> AlbIdentity | None:
    """Return the verified user, or ``None`` if identity cannot be established.

    Deliberately fails closed and never raises: a caller that cannot get an
    identity must treat the request as unauthenticated rather than fall back to
    an assumed role.
    """

    if not region or not expected_alb_arn or not user_pool_id:
        # Unconfigured is not the same as unauthenticated, but both must deny.
        logger.warning("ALB identity verification is not configured; denying identity.")
        return None

    data_token = _header(headers, OIDC_DATA_HEADER)
    access_token = _header(headers, OIDC_ACCESS_TOKEN_HEADER)
    if not data_token or not access_token:
        return None

    try:
        user_claims = _verify_alb_assertion(
            data_token,
            region=region,
            expected_alb_arn=expected_alb_arn,
            keys=keys or _alb_keys,
        )
        access_claims = _verify_cognito_access_token(
            access_token,
            region=region,
            user_pool_id=user_pool_id,
            expected_client_id=expected_client_id,
        )
    except AlbIdentityError as exc:
        # Log the reason but never the token contents.
        logger.warning("Rejected ALB identity headers: %s", exc)
        return None

    # Prefer the ALB's own subject header, falling back to the verified claims.
    subject = (
        _header(headers, OIDC_IDENTITY_HEADER)
        or str(user_claims.get("sub") or "")
        or str(access_claims.get("sub") or "")
    ).strip()
    if not subject:
        logger.warning("Rejected ALB identity headers: no subject claim present.")
        return None

    # The two headers must describe the same person. They are independently
    # signed, so a mismatch means they were not issued for the same session.
    access_subject = str(access_claims.get("sub") or "").strip()
    if access_subject and access_subject != subject:
        logger.warning("Rejected ALB identity headers: subject mismatch across headers.")
        return None

    groups = _extract_groups(access_claims)
    if not groups:
        logger.warning("Rejected ALB identity headers: no Cognito group claim present.")
        return None

    email = str(user_claims.get("email") or "").strip() or None
    return AlbIdentity(subject=subject, groups=groups, email=email)


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header read that tolerates plain dicts in tests."""

    value = headers.get(name)
    if value is None:
        for key, candidate in headers.items():
            if str(key).lower() == name:
                value = candidate
                break
    return str(value or "").strip()
