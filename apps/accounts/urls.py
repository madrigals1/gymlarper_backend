from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.core.routers import ActionOnlyRouter

from .views import AuthViewSet, DeviceSessionViewSet

# Routed rather than hand-wired with as_view(): only a router applies the
# per-action kwargs from @action (permission_classes, throttle_scope, ...).
auth_router = ActionOnlyRouter()
auth_router.register("auth", AuthViewSet, basename="auth")

device_router = DefaultRouter()
device_router.register("", DeviceSessionViewSet, basename="device")

urlpatterns = [
    path("", include(auth_router.urls)),
    path("auth/devices/", include(device_router.urls)),
]
