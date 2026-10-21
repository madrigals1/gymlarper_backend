from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic.base import RedirectView


def healthcheck(_request):
    """Liveness probe used by Docker Compose and any external monitor."""
    return JsonResponse({"status": "ok", "service": "gymlarper-api"})


urlpatterns = [
    # The root is not part of the API surface; send visitors to the versioned
    # root instead of a 404. Temporary (302) so a future /api/v2/ can take over
    # without clients having cached a permanent redirect.
    path("", RedirectView.as_view(url="/api/v1/"), name="root"),
    path("admin/", admin.site.urls),
    path("healthz", healthcheck, name="healthcheck"),
    path("api/v1/", include(("apps.accounts.urls", "accounts"), namespace="accounts")),
    path("api/v1/", include(("apps.workouts.urls", "workouts"), namespace="workouts")),
]
