from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthcheck(_request):
    """Liveness probe used by Docker Compose and any external monitor."""
    return JsonResponse({"status": "ok", "service": "gymlarper-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthcheck, name="healthcheck"),
    path("api/v1/", include(("apps.accounts.urls", "accounts"), namespace="accounts")),
    path("api/v1/", include(("apps.workouts.urls", "workouts"), namespace="workouts")),
]
