from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("locations", views.GymLocationViewSet, basename="location")
router.register("exercises", views.ExerciseViewSet, basename="exercise")
router.register("machine-setups", views.MachineSetupViewSet, basename="machine-setup")
router.register("templates", views.WorkoutTemplateViewSet, basename="template")
router.register("workouts", views.WorkoutViewSet, basename="workout")
router.register("workout-exercises", views.WorkoutExerciseViewSet, basename="workout-exercise")
router.register("sets", views.WorkoutSetViewSet, basename="workout-set")

urlpatterns = [
    path("sync/push/", views.SyncPushView.as_view(), name="sync-push"),
    path("sync/pull/", views.SyncPullView.as_view(), name="sync-pull"),
    path("dashboard/summary/", views.dashboard_summary, name="dashboard-summary"),
    path("dashboard/weekly-volume/", views.weekly_volume, name="weekly-volume"),
    path("dashboard/personal-records/", views.personal_records, name="personal-records"),
    path("dashboard/volume-by-muscle/", views.volume_by_muscle, name="volume-by-muscle"),
    path("dashboard/machines/", views.machines, name="dashboard-machines"),
    path(
        "dashboard/exercise-progress/<uuid:exercise_id>/",
        views.exercise_progress,
        name="exercise-progress",
    ),
    path("", include(router.urls)),
]
