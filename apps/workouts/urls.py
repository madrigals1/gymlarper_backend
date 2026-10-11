from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.core.routers import ActionOnlyRouter

from . import views

# Routed rather than hand-wired with as_view(): only a router applies the
# per-action kwargs from @action (throttle_scope, ...).
action_router = ActionOnlyRouter()
action_router.register("sync", views.SyncViewSet, basename="sync")
action_router.register("dashboard", views.DashboardViewSet, basename="dashboard")

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
    path("", include(action_router.urls)),
    path("", include(router.urls)),
]
