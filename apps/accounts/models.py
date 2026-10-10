from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    """Per-user preferences that are not part of Django's auth model."""

    class Units(models.TextChoices):
        KG = "kg", "Kilograms"
        LB = "lb", "Pounds"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    google_sub = models.CharField(max_length=255, unique=True, null=True, blank=True)
    google_email = models.EmailField(blank=True)
    google_picture = models.URLField(blank=True)
    google_email_verified = models.BooleanField(default=False)
    google_name = models.CharField(max_length=150, blank=True)

    units = models.CharField(max_length=2, choices=Units.choices, default=Units.KG)

    # Per-user defaults for how new workouts are recorded.
    default_location = models.ForeignKey(
        "workouts.GymLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_users",
    )
    bodyweight_kg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "user profile"
        verbose_name_plural = "user profiles"

    def __str__(self) -> str:
        return f"profile({self.user.username})"

    def save(self, *args, **kwargs) -> None:
        if not self.pk:
            existing = UserProfile.objects.filter(user_id=self.user_id).first()
            if existing:
                # Auto-created by the post_save signal; do not duplicate.
                for field in self._meta.fields:
                    if field.primary_key:
                        continue
                    setattr(self, field.attname, getattr(existing, field.attname))
                self._state.adding = False
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        if self.google_name:
            return self.google_name
        if self.google_email:
            return self.google_email.split("@")[0]
        return self.user.username or self.user.get_full_name() or "athlete"


class DeviceSession(models.Model):
    """One row per signed-in device, so refresh tokens can be revoked individually.

    The Flutter client stores the opaque session token; only its hash lives here,
    so a database leak does not hand out working sessions.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    device_name = models.CharField(max_length=120, blank=True)
    platform = models.CharField(max_length=32, blank=True)
    app_version = models.CharField(max_length=32, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["user", "-last_seen_at"])]

    def __str__(self) -> str:
        state = "revoked" if self.revoked_at else "active"
        return f"{self.device_name or 'unknown device'} ({state})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


def ensure_profile(user: User) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile