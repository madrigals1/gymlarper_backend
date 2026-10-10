from __future__ import annotations

from django.contrib.auth.models import User
from rest_framework import serializers

from apps.core.serializers import OwnerScopedRelationsMixin

from .models import DeviceSession, UserProfile


class UserProfileSerializer(OwnerScopedRelationsMixin, serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "email",
            "display_name",
            "google_picture",
            "units",
            "default_location",
            "bodyweight_kg",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "email",
            "display_name",
            "google_picture",
            "created_at",
            "updated_at",
        )


class GoogleSignInSerializer(serializers.Serializer):
    """Payload posted by the Flutter client straight after a Google sign-in."""

    id_token = serializers.CharField(write_only=True, trim_whitespace=False)
    device_name = serializers.CharField(
        required=False, allow_blank=True, max_length=120
    )
    platform = serializers.CharField(required=False, allow_blank=True, max_length=32)
    app_version = serializers.CharField(required=False, allow_blank=True, max_length=32)


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True, trim_whitespace=False)


class DeviceSessionSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = DeviceSession
        fields = (
            "id",
            "device_name",
            "platform",
            "app_version",
            "created_at",
            "last_seen_at",
            "revoked_at",
            "is_active",
        )
        read_only_fields = fields


class ChangePasswordSerializer(serializers.Serializer):
    """Setting a password requires proving control of the account.

    Accounts created through Google have an *unusable* password, so there is
    nothing to check against - demanding a current password would lock those
    users out entirely. Instead they must re-present a fresh Google ID token.
    """

    current_password = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    new_password = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    google_id_token = serializers.CharField(
        write_only=True, required=False, allow_blank=True, trim_whitespace=False
    )

    def validate(self, attrs):
        if not attrs.get("new_password"):
            raise serializers.ValidationError(
                {"new_password": "This field is required."}
            )
        if len(attrs["new_password"]) < 8:
            raise serializers.ValidationError(
                {"new_password": "Use at least 8 characters."}
            )

        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(attrs["new_password"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"new_password": list(exc.messages)}
            ) from exc
        return attrs


class AdminUserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "email", "is_active", "date_joined", "profile")
