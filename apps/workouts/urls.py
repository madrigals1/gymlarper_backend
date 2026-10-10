from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("locations", views.GymLocationViewSet, basename="location")
router.register("exercises", views.ExerciseViewSet, basename="exercise")
router.register("machine-setups", views.MachineSetupViewSet, basename="machine-setup")
router.register("templates", views.WorkoutTemplateViewSet, basename="template")
router.register("workouts", views.WorkoutViewSet, basename="workout")
router.register(
    "workout-exercises", views.WorkoutExerciseViewSet, basename="workout-exercise"
)
router.register("sets", views.WorkoutSetViewSet, basename="workout-set")

urlpatterns = [
    path(
        "sync/push/",
        views.SyncViewSet.as_view({"post": "push"}),
        name="sync-push",
    ),
    path(
        "sync/pull/",
        views.SyncViewSet.as_view({"get": "pull"}),
        name="sync-pull",
    ),
    path(
        "dashboard/summary/",
        views.DashboardViewSet.as_view({"get": "summary"}),
        name="dashboard-summary",
    ),
    path(
        "dashboard/weekly-volume/",
        views.DashboardViewSet.as_view({"get": "weekly_volume"}),
        name="weekly-volume",
    ),
    path(
        "dashboard/personal-records/",
        views.DashboardViewSet.as_view({"get": "personal_records"}),
        name="personal-records",
    ),
    path(
        "dashboard/volume-by-muscle/",
        views.DashboardViewSet.as_view({"get": "volume_by_muscle"}),
        name="volume-by-muscle",
    ),
    path(
        "dashboard/machines/",
        views.DashboardViewSet.as_view({"get": "machines"}),
        name="dashboard-machines",
    ),
    path(
        "dashboard/exercise-progress/<uuid:exercise_id>/",
        views.DashboardViewSet.as_view({"get": "exercise_progress"}),
        name="exercise-progress",
    ),
    path("", include(router.urls)),
]