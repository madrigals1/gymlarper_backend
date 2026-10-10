from django.urls import path

from .views import (
    ChangePasswordView,
    DeviceSessionListView,
    DeviceSessionRevokeView,
    GoogleSignInView,
    LogoutView,
    MeView,
    RefreshView,
)

urlpatterns = [
    path("auth/google/", GoogleSignInView.as_view(), name="google-signin"),
    path("auth/refresh/", RefreshView.as_view(), name="refresh"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/password/", ChangePasswordView.as_view(), name="change-password"),
    path("auth/devices/", DeviceSessionListView.as_view(), name="devices"),
    path(
        "auth/devices/<int:pk>/revoke/",
        DeviceSessionRevokeView.as_view(),
        name="device-revoke",
    ),
]
