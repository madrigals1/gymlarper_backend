from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AuthViewSet, DeviceSessionViewSet

device_router = DefaultRouter()
device_router.register("", DeviceSessionViewSet, basename="device")

urlpatterns = [
    path("auth/google/", AuthViewSet.as_view({"post": "google"}), name="google-signin"),
    path("auth/refresh/", AuthViewSet.as_view({"post": "refresh"}), name="refresh"),
    path("auth/logout/", AuthViewSet.as_view({"post": "logout"}), name="logout"),
    path(
        "auth/me/",
        AuthViewSet.as_view({"get": "me", "patch": "me"}),
        name="me",
    ),
    path(
        "auth/password/",
        AuthViewSet.as_view({"post": "change_password"}),
        name="change-password",
    ),
    path("auth/devices/", include(device_router.urls)),
]