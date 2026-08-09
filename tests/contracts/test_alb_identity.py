"""Verification contracts for ALB-signed identity headers.

These tests sign real tokens with real keys rather than mocking the verifier,
because the whole value of this module is that a forged or altered header is
rejected. A mocked signature check would pass while proving nothing.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.core import alb_identity
from app.core.alb_identity import (
    OIDC_ACCESS_TOKEN_HEADER,
    OIDC_DATA_HEADER,
    OIDC_IDENTITY_HEADER,
    _AlbPublicKeys,
    resolve_alb_identity,
)

REGION = "us-east-1"
USER_POOL_ID = "us-east-1_Example1"
CLIENT_ID = "1example23456789"
ALB_ARN = (
    "arn:aws:elasticloadbalancing:us-east-1:862772137583:"
    "loadbalancer/app/lcdash-p1-logan-use1-alb/abc123"
)
OTHER_ALB_ARN = ALB_ARN.replace("lcdash-p1-logan-use1-alb", "someone-elses-alb")
SUBJECT = "11111111-2222-3333-4444-555555555555"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
KEY_ID = "key-1"


def _pem(public_key) -> str:
    from cryptography.hazmat.primitives import serialization

    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


class _StubJwk:
    def __init__(self, key) -> None:
        self.key = key


class _StubJwkClient:
    def __init__(self, key) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> _StubJwk:  # noqa: ARG002
        return _StubJwk(self._key)


class AlbIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.alb_private = ec.generate_private_key(ec.SECP256R1())
        self.cognito_private = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        alb_pem = _pem(self.alb_private.public_key())
        # Inject the key fetcher so no test touches the network.
        self.keys = _AlbPublicKeys(fetch=lambda url, purpose: alb_pem)

        patcher = patch.object(
            alb_identity,
            "_cognito_jwk_client",
            return_value=_StubJwkClient(self.cognito_private.public_key()),
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    # ---- token builders -------------------------------------------------

    def _alb_token(
        self,
        *,
        signer: str = ALB_ARN,
        expires_at: float | None = None,
        subject: str = SUBJECT,
        algorithm: str = "ES256",
        key=None,
    ) -> str:
        headers = {
            "kid": KEY_ID,
            "signer": signer,
            "exp": expires_at if expires_at is not None else time.time() + 300,
        }
        return jwt.encode(
            {"sub": subject, "email": "dispatcher@911logan.com"},
            key if key is not None else self.alb_private,
            algorithm=algorithm,
            headers=headers,
        )

    def _access_token(
        self,
        *,
        groups=("lcdash-pilot-reviewer",),
        token_use: str = "access",
        issuer: str = ISSUER,
        client_id: str = CLIENT_ID,
        subject: str = SUBJECT,
        expires_in: int = 300,
        key=None,
    ) -> str:
        claims = {
            "sub": subject,
            "iss": issuer,
            "token_use": token_use,
            "client_id": client_id,
            "exp": int(time.time()) + expires_in,
        }
        if groups is not None:
            claims["cognito:groups"] = list(groups)
        return jwt.encode(
            claims,
            key if key is not None else self.cognito_private,
            algorithm="RS256",
        )

    def _resolve(self, headers, **overrides):
        kwargs = {
            "region": REGION,
            "expected_alb_arn": ALB_ARN,
            "user_pool_id": USER_POOL_ID,
            "expected_client_id": CLIENT_ID,
            "keys": self.keys,
        }
        kwargs.update(overrides)
        return resolve_alb_identity(headers, **kwargs)

    def _headers(self, **overrides):
        headers = {
            OIDC_DATA_HEADER: self._alb_token(),
            OIDC_ACCESS_TOKEN_HEADER: self._access_token(),
            OIDC_IDENTITY_HEADER: SUBJECT,
        }
        headers.update(overrides)
        return headers

    # ---- the happy path -------------------------------------------------

    def test_verified_headers_yield_subject_and_groups(self):
        identity = self._resolve(self._headers())
        self.assertIsNotNone(identity)
        self.assertEqual(identity.subject, SUBJECT)
        self.assertEqual(identity.groups, ("lcdash-pilot-reviewer",))
        self.assertEqual(identity.email, "dispatcher@911logan.com")

    def test_groups_arriving_as_a_delimited_string_are_still_read(self):
        token = jwt.encode(
            {
                "sub": SUBJECT,
                "iss": ISSUER,
                "token_use": "access",
                "client_id": CLIENT_ID,
                "exp": int(time.time()) + 300,
                "cognito:groups": "lcdash-pilot-viewer,lcdash-pilot-reviewer",
            },
            self.cognito_private,
            algorithm="RS256",
        )
        identity = self._resolve(self._headers(**{OIDC_ACCESS_TOKEN_HEADER: token}))
        self.assertIsNotNone(identity)
        self.assertEqual(
            set(identity.groups), {"lcdash-pilot-viewer", "lcdash-pilot-reviewer"}
        )

    # ---- provenance: the signer check -----------------------------------

    def test_assertion_signed_by_another_load_balancer_is_rejected(self):
        """The signer check is the whole anti-forgery story; it must hold."""
        headers = self._headers(
            **{OIDC_DATA_HEADER: self._alb_token(signer=OTHER_ALB_ARN)}
        )
        self.assertIsNone(self._resolve(headers))

    def test_assertion_with_no_signer_is_rejected(self):
        headers = self._headers(**{OIDC_DATA_HEADER: self._alb_token(signer="")})
        self.assertIsNone(self._resolve(headers))

    def test_assertion_signed_by_an_unrelated_key_is_rejected(self):
        stranger = ec.generate_private_key(ec.SECP256R1())
        headers = self._headers(**{OIDC_DATA_HEADER: self._alb_token(key=stranger)})
        self.assertIsNone(self._resolve(headers))

    def test_tampered_assertion_payload_is_rejected(self):
        token = self._alb_token()
        header_b64, payload_b64, signature = token.split(".")
        # Swap in a different payload while keeping the original signature.
        forged_payload = (
            jwt.encode({"sub": "attacker"}, self.alb_private, algorithm="ES256")
            .split(".")[1]
        )
        headers = self._headers(
            **{OIDC_DATA_HEADER: f"{header_b64}.{forged_payload}.{signature}"}
        )
        self.assertIsNone(self._resolve(headers))

    def test_expired_assertion_is_rejected(self):
        """Expiry lives in the ALB JWT header, which PyJWT does not police."""
        headers = self._headers(
            **{OIDC_DATA_HEADER: self._alb_token(expires_at=time.time() - 1)}
        )
        self.assertIsNone(self._resolve(headers))

    def test_unsigned_assertion_is_rejected(self):
        """An alg=none token must never be accepted."""
        token = jwt.encode({"sub": SUBJECT}, key="", algorithm="none", headers={
            "kid": KEY_ID, "signer": ALB_ARN, "exp": time.time() + 300
        })
        headers = self._headers(**{OIDC_DATA_HEADER: token})
        self.assertIsNone(self._resolve(headers))

    def test_symmetric_algorithm_substitution_is_rejected(self):
        """Algorithm confusion: HS256 signed with the ALB's own public key.

        Hand-rolled rather than built with ``jwt.encode``, which refuses to use
        a PEM as an HMAC secret. An attacker has no such scruples, so the token
        is assembled byte by byte to make the attack real.
        """
        import base64
        import hashlib
        import hmac
        import json as _json

        def b64(raw: bytes) -> bytes:
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        alb_pem = _pem(self.alb_private.public_key()).encode("utf-8")
        header = b64(
            _json.dumps(
                {
                    "alg": "HS256",
                    "kid": KEY_ID,
                    "signer": ALB_ARN,
                    "exp": time.time() + 300,
                }
            ).encode("utf-8")
        )
        payload = b64(_json.dumps({"sub": SUBJECT}).encode("utf-8"))
        signing_input = header + b"." + payload
        signature = b64(
            hmac.new(alb_pem, signing_input, hashlib.sha256).digest()
        )
        token = (signing_input + b"." + signature).decode("ascii")

        headers = self._headers(**{OIDC_DATA_HEADER: token})
        self.assertIsNone(self._resolve(headers))

    # ---- authorization: the Cognito access token ------------------------

    def test_access_token_from_another_pool_is_rejected(self):
        other_issuer = f"https://cognito-idp.{REGION}.amazonaws.com/us-east-1_Other999"
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(issuer=other_issuer)}
        )
        self.assertIsNone(self._resolve(headers))

    def test_access_token_signed_by_an_unrelated_key_is_rejected(self):
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(key=stranger)}
        )
        self.assertIsNone(self._resolve(headers))

    def test_id_token_presented_as_an_access_token_is_rejected(self):
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(token_use="id")}
        )
        self.assertIsNone(self._resolve(headers))

    def test_access_token_for_another_client_is_rejected(self):
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(client_id="9other8")}
        )
        self.assertIsNone(self._resolve(headers))

    def test_expired_access_token_is_rejected(self):
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(expires_in=-60)}
        )
        self.assertIsNone(self._resolve(headers))

    def test_absent_group_claim_denies_rather_than_defaulting(self):
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(groups=None)}
        )
        self.assertIsNone(self._resolve(headers))

    def test_empty_group_claim_denies(self):
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(groups=())}
        )
        self.assertIsNone(self._resolve(headers))

    # ---- header pairing -------------------------------------------------

    def test_headers_describing_different_people_are_rejected(self):
        """Independently signed headers must agree on who the user is."""
        headers = self._headers(
            **{
                OIDC_ACCESS_TOKEN_HEADER: self._access_token(subject="someone-else"),
                OIDC_IDENTITY_HEADER: SUBJECT,
            }
        )
        self.assertIsNone(self._resolve(headers))

    def test_missing_access_token_header_is_rejected(self):
        headers = self._headers()
        headers.pop(OIDC_ACCESS_TOKEN_HEADER)
        self.assertIsNone(self._resolve(headers))

    def test_missing_assertion_header_is_rejected(self):
        headers = self._headers()
        headers.pop(OIDC_DATA_HEADER)
        self.assertIsNone(self._resolve(headers))

    def test_no_headers_at_all_is_rejected(self):
        self.assertIsNone(self._resolve({}))

    def test_header_lookup_is_case_insensitive(self):
        headers = {
            OIDC_DATA_HEADER.upper(): self._alb_token(),
            "X-Amzn-Oidc-Accesstoken": self._access_token(),
        }
        self.assertIsNotNone(self._resolve(headers))

    # ---- configuration --------------------------------------------------

    def test_missing_load_balancer_arn_denies_rather_than_skipping_the_check(self):
        """Unconfigured must fail closed, never 'verify nothing and allow'."""
        self.assertIsNone(self._resolve(self._headers(), expected_alb_arn=""))

    def test_missing_user_pool_id_denies(self):
        self.assertIsNone(self._resolve(self._headers(), user_pool_id=""))

    def test_client_id_check_is_skipped_only_when_not_configured(self):
        headers = self._headers(
            **{OIDC_ACCESS_TOKEN_HEADER: self._access_token(client_id="9other8")}
        )
        self.assertIsNotNone(self._resolve(headers, expected_client_id=None))

    def test_tokens_are_never_written_to_logs(self):
        headers = self._headers(
            **{OIDC_DATA_HEADER: self._alb_token(signer=OTHER_ALB_ARN)}
        )
        with self.assertLogs("app.core.alb_identity", level="WARNING") as captured:
            self.assertIsNone(self._resolve(headers))
        logged = "\n".join(captured.output)
        self.assertNotIn(headers[OIDC_DATA_HEADER], logged)
        self.assertNotIn(headers[OIDC_ACCESS_TOKEN_HEADER], logged)


if __name__ == "__main__":
    unittest.main()
