"""Google OAuth2 ID-token verification.

The Flutter client signs in with ``google_sign_in``, which returns a short-lived
JWT issued by Google. We verify it here and issue our own access/refresh pair.

Why a server is involved at all: verification itself could be done on-device
against Google's public JWKS, but sessions, revocation and cross-device sync all
need a trusted store. A client-supplied token is never enough on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


class GoogleAuthError(Exception):
    """Raised when an ID token cannot be trusted."""

    def __init__(self, message: str, *, code: str = "invalid_token") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class GoogleNotConfigured(GoogleAuthError):
    def __init__(self) -> None:
        super().__init__(
            "GOOGLE_CLIENT_IDS is not configured on the server.", code="not_configured"
        )


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str
    picture: str


def _fetch_verified_claims(id_token_value: str) -> dict:
    """Signature + issuer + expiry check via Google's JWKS (cached by the library)."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    # audience=None: this app legitimately has several client IDs (Web, Android,
    # iOS) and we check the audience ourselves against the allow-list below.
    return google_id_token.verify_oauth2_token(
        id_token_value,
        google_requests.Request(),
        audience=None,
        clock_skew_in_seconds=30,
    )


def verify_google_token(id_token_value: str) -> GoogleIdentity:
    """Validate a Google ID token and return the caller's identity.

    Raises :class:`GoogleAuthError` if the token is missing, expired, signed by
    the wrong party, or addressed to a client this server does not serve.
    """
    allowed = [
        client_id
        for client_id in (getattr(settings, "GOOGLE_CLIENT_IDS", None) or [])
        if str(client_id).strip()
    ]
    if not allowed:
        # Fail closed. Never accept an unverified token just because the
        # deployment forgot to set an environment variable.
        logger.error("GOOGLE_CLIENT_IDS is empty; refusing to verify tokens.")
        raise GoogleNotConfigured()

    if not id_token_value or not isinstance(id_token_value, str):
        raise GoogleAuthError("An ID token is required.")

    try:
        claims = _fetch_verified_claims(id_token_value)
    except GoogleAuthError:
        raise
    except Exception as exc:  # google.auth raises ValueError / TransportError
        logger.info("Google ID token verification failed: %s", exc)
        raise GoogleAuthError("Google could not verify this sign-in token.") from exc

    audience = claims.get("azp") or claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if not any(aud in allowed for aud in audiences if aud):
        logger.warning("Rejected token with unexpected audience: %r", audience)
        raise GoogleAuthError("This sign-in was not issued for this application.")

    issuer = claims.get("iss", "")
    if issuer not in {"https://accounts.google.com", "accounts.google.com"}:
        raise GoogleAuthError("Untrusted token issuer.")

    sub = claims.get("sub")
    email = (claims.get("email") or "").lower()
    if not sub or not email:
        raise GoogleAuthError("Google did not return an email address.")

    hd = getattr(settings, "GOOGLE_HD_ORG_DOMAIN", "")
    if hd and (claims.get("hd") or "").lower() != hd.lower():
        raise GoogleAuthError(
            "This account is not part of the allowed organisation.", code="wrong_domain"
        )

    return GoogleIdentity(
        sub=sub,
        email=email,
        email_verified=bool(claims.get("email_verified")),
        name=claims.get("name") or email.split("@")[0],
        picture=claims.get("picture") or "",
    )