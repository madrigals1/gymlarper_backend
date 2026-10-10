from __future__ import annotations

import hashlib
import logging
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .google import GoogleAuthError, GoogleIdentity, verify_google_token
from .models import DeviceSession, UserProfile, ensure_profile
from .serializers import (
    ChangePasswordSerializer,
    DeviceSessionSerializer,
    GoogleSignInSerializer,
    RefreshSerializer,
    UserProfileSerializer,
)

logger = logging.getLogger(__name__)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _access_for(refresh: RefreshToken, session: DeviceSession) -> str:
    """Mint the access token, stamping the device session it belongs to."""
    token = refresh.access_token
    token.payload["sid"] = session.pk
    return str(token)


def _username_for(identity: GoogleIdentity) -> str:
    base = (identity.email.split("@")[0] or identity.sub)[:140]
    return f"g_{base}_{secrets.token_hex(3)}"


@transaction.atomic
def resolve_user(identity: GoogleIdentity) -> tuple[User, bool]:
    """Find or create the Django user behind a verified Google identity."""
    profile = UserProfile.objects.filter(google_sub=identity.sub).select_related("user").first()
    if profile:
        user = profile.user
        created = False
    else:
        user = User.objects.filter(email__iexact=identity.email).first()
        if user:
            created = False
        else:
            try:
                user = User.objects.create_user(
                    username=_username_for(identity), email=identity.email
                )
                created = True
            except IntegrityError:
                # Two requests raced on the same email; fall through and re-read.
                user = User.objects.filter(email__iexact=identity.email).first()
                if user is None:
                    raise
                created = False

    profile = ensure_profile(user)

    # Never rebind an existing Google subject. Doing so would let anyone who
    # controls an unverified address claim an existing account and lock the
    # original owner out.
    if profile.google_sub and profile.google_sub != identity.sub:
        raise GoogleAuthError(
            "This account is already linked to a different Google identity.",
            code="identity_conflict",
        )

    profile.google_sub = identity.sub
    profile.google_email = identity.email
    profile.google_picture = identity.picture
    profile.google_email_verified = identity.email_verified
    profile.google_name = identity.name
    profile.save(
        update_fields=[
            "google_sub",
            "google_email",
            "google_picture",
            "google_email_verified",
            "google_name",
            "updated_at",
        ]
    )
    return user, created


def issue_tokens(user: User, *, device_name: str, platform: str, app_version: str) -> dict:
    """Mint an access/refresh pair and record the device it belongs to."""
    refresh = RefreshToken.for_user(user)
    session = DeviceSession.objects.create(
        user=user,
        token_hash=hash_token(str(refresh)),
        device_name=device_name[:120],
        platform=platform[:32],
        app_version=app_version[:32],
    )
    return {
        "access": _access_for(refresh, session),
        "refresh": str(refresh),
        "session_id": session.id,
        "expires_in": int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
    }


class GoogleSignInView(APIView):
    """Exchange a verified Google ID token for our own access/refresh pair."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "google_signin"

    def post(self, request):
        serializer = GoogleSignInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            identity = verify_google_token(data["id_token"])
            # Google is authoritative for the address; an unverified one must
            # not be able to claim an existing account by email alone.
            if not identity.email_verified:
                raise GoogleAuthError(
                    "Google reports this email as unverified.", code="unverified_email"
                )
            user, created = resolve_user(identity)
        except GoogleAuthError as exc:
            logger.info("Google sign-in rejected: %s", exc)
            status_code = (
                status.HTTP_409_CONFLICT
                if exc.code == "identity_conflict"
                else status.HTTP_401_UNAUTHORIZED
            )
            return Response(
                {"error": {"code": exc.code, "message": exc.message, "details": {}}},
                status=status_code,
            )

        tokens = issue_tokens(
            user,
            device_name=data.get("device_name", ""),
            platform=data.get("platform", ""),
            app_version=data.get("app_version", ""),
        )
        profile = ensure_profile(user)
        return Response(
            {
                **tokens,
                "is_new_user": created,
                "user": UserProfileSerializer(profile).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class RefreshView(APIView):
    """Rotate a refresh token, returning a fresh pair and the same session."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "token_refresh"

    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data["refresh"]

        from rest_framework_simplejwt.exceptions import TokenError

        try:
            token = RefreshToken(raw)
        except TokenError:
            return Response(
                {"error": {"code": "invalid_refresh", "message": "Session expired.", "details": {}}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        session = (
            DeviceSession.objects.select_for_update()
            .filter(token_hash=hash_token(raw))
            .first()
        )
        if session is None or not session.is_active:
            # Either signed out, or the token was rotated away. Either way this
            # token must never be accepted again.
            return Response(
                {"error": {"code": "session_revoked", "message": "Session is no longer valid.", "details": {}}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user = session.user
        if not user.is_active:
            return Response(
                {"error": {"code": "account_disabled", "message": "Account is disabled.", "details": {}}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        new_refresh = RefreshToken.for_user(user)
        session.token_hash = hash_token(str(new_refresh))
        session.last_seen_at = timezone.now()
        session.save(update_fields=["token_hash", "last_seen_at"])

        return Response(
            {
                "access": _access_for(new_refresh, session),
                "refresh": str(new_refresh),
                "session_id": session.id,
                "expires_in": int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
            }
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _touch_session(request)
        return Response(UserProfileSerializer(ensure_profile(request.user)).data)

    def patch(self, request):
        profile = ensure_profile(request.user)
        serializer = UserProfileSerializer(
            profile, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


def _touch_session(request) -> None:
    """Keep 'last seen' honest for the active-devices screen."""
    session_id = (getattr(request, "auth", None) or {}).get("sid") if hasattr(request, "auth") else None
    if session_id:
        DeviceSession.objects.filter(pk=session_id).update(last_seen_at=timezone.now())


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        DeviceSession.objects.filter(user=request.user, revoked_at__isnull=True).update(
            revoked_at=now
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceSessionListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = DeviceSessionSerializer

    def get_queryset(self):
        return DeviceSession.objects.filter(user=self.request.user, revoked_at__isnull=True)


class DeviceSessionRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk: int):
        updated = DeviceSession.objects.filter(
            user=request.user, pk=pk, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        if not updated:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangePasswordView(APIView):
    """Escape hatch for accounts that cannot (or do not want to) use Google.

    Because Google accounts have no usable password, "prove you are you" means
    re-verifying a Google ID token for the *same* subject. Otherwise a stolen
    access token could mint a permanent password that survives every later
    session revocation.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user

        if user.has_usable_password():
            if not user.check_password(serializer.validated_data.get("current_password", "")):
                return Response(
                    {
                        "error": {
                            "code": "invalid_credentials",
                            "message": "Current password is incorrect.",
                            "details": {},
                        }
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            token = serializer.validated_data.get("google_id_token", "")
            try:
                identity = verify_google_token(token)
            except GoogleAuthError as exc:
                return Response(
                    {
                        "error": {
                            "code": exc.code,
                            "message": "Re-authenticate with Google to set a password.",
                            "details": {},
                        }
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            profile = ensure_profile(user)
            if not profile.google_sub or profile.google_sub != identity.sub:
                return Response(
                    {
                        "error": {
                            "code": "identity_conflict",
                            "message": "That Google account is not linked to this user.",
                            "details": {},
                        }
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response({"detail": "Password updated."})