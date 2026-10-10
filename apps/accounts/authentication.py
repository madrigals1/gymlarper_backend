"""Authentication that honours device revocation immediately.

A bare JWT is stateless: signing out cannot touch tokens that are already in the
wild without a lookup. For a workout app that is not good enough - "sign out this
device" has to mean now, not in up to an hour.

Every access token therefore carries the ``sid`` of the :class:`DeviceSession`
that minted it, and this authentication class confirms that session is still live
on each request. The cost is one indexed primary-key read per request.
"""

from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken

from .models import DeviceSession


class SessionBoundJWTAuthentication(JWTAuthentication):
    """Reject access tokens whose device session has been revoked."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        session_id = validated_token.get("sid")
        if session_id is None:
            # Tokens minted before session binding was introduced. Accepting
            # them would leave a revocation bypass, so require a fresh sign-in.
            raise InvalidToken("Session-bound token required; please sign in again.")

        session = DeviceSession.objects.filter(pk=session_id).only("user_id", "revoked_at").first()
        if session is None or not session.is_active:
            raise AuthenticationFailed("This session has been signed out.")
        if session.user_id != user.pk:
            raise AuthenticationFailed("Token does not belong to this session.")

        return user
