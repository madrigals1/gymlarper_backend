from django.contrib import admin

from .models import DeviceSession, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "google_email", "units", "default_location", "updated_at")
    search_fields = ("google_email", "user__username", "user__email")
    list_filter = ("units",)


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "device_name", "platform", "last_seen_at", "revoked_at")
    list_filter = ("platform",)
    search_fields = ("user__email", "device_name")