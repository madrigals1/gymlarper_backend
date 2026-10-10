from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.workouts.models import (
    Exercise,
    GymLocation,
    MachineSetup,
    Workout,
    WorkoutExercise,
    WorkoutSet,
)


class ApiTestCase(TestCase):
    """Shared helpers for API-level tests."""

    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(username="ada", email="ada@example.com")
        self.other = User.objects.create_user(username="bob", email="bob@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def authed(self, user=None) -> APIClient:
        api = APIClient()
        api.force_authenticate(user or self.user)
        return api

    def make_exercise(self, **kwargs) -> Exercise:
        defaults = {"user": self.user, "name": "Leg Press", "equipment": "machine"}
        defaults.update(kwargs)
        return Exercise.objects.create(**defaults)


class WorkoutLifecycleTests(ApiTestCase):
    def test_start_finish_workout(self):
        response = self.client.post(
            "/api/v1/workouts/start/", {"name": "Evening"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        workout_id = response.json()["id"]
        self.assertIsNone(response.json()["finished_at"])
        self.assertTrue(response.json()["is_active"])

        response = self.client.post(
            f"/api/v1/workouts/{workout_id}/finish/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["finished_at"])
        self.assertFalse(response.json()["is_active"])
        self.assertEqual(response.json()["duration_seconds"], 0)

    def test_start_twice_returns_the_same_session(self):
        first = self.client.post("/api/v1/workouts/start/", {}, format="json")
        second = self.client.post("/api/v1/workouts/start/", {}, format="json")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Workout.objects.filter(user=self.user).count(), 1)

    def test_active_endpoint_404_when_idle_then_finds_session(self):
        self.assertEqual(self.client.get("/api/v1/workouts/active/").status_code, 404)
        self.client.post("/api/v1/workouts/start/", {}, format="json")
        self.assertEqual(self.client.get("/api/v1/workouts/active/").status_code, 200)

    def test_cannot_finish_another_users_workout(self):
        victim = Workout.objects.create(user=self.other, started_at=timezone.now())
        response = self.client.post(
            f"/api/v1/workouts/{victim.id}/finish/", {}, format="json"
        )
        self.assertEqual(response.status_code, 404)


class SetLoggingTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.workout = Workout.objects.create(user=self.user, started_at=timezone.now())
        self.exercise = self.make_exercise()
        self.workout_exercise = WorkoutExercise.objects.create(
            user=self.user,
            workout=self.workout,
            exercise=self.exercise,
            exercise_name=self.exercise.name,
            muscle_group=self.exercise.muscle_group,
        )

    def test_complete_set_records_volume_and_1rm(self):
        workout_set = WorkoutSet.objects.create(
            user=self.user,
            workout_exercise=self.workout_exercise,
            reps=10,
            weight=Decimal("100"),
            unit="kg",
        )
        response = self.client.post(
            f"/api/v1/sets/{workout_set.id}/complete/",
            {"is_completed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["is_completed"])
        self.assertIsNotNone(body["completed_at"])
        self.assertEqual(float(body["volume_kg"]), 1000.0)
        # Epley: 100 * (1 + 10/30) = 133.33
        self.assertAlmostEqual(body["epley_1rm"], 133.33, places=1)

    def test_unchecking_clears_timestamp_and_volume(self):
        workout_set = WorkoutSet.objects.create(
            user=self.user,
            workout_exercise=self.workout_exercise,
            reps=5,
            weight=Decimal("60"),
            unit="kg",
            is_completed=True,
            completed_at=timezone.now(),
        )
        response = self.client.post(
            f"/api/v1/sets/{workout_set.id}/complete/",
            {"is_completed": False},
            format="json",
        )
        self.assertIsNone(response.json()["completed_at"])
        self.assertEqual(float(response.json()["volume_kg"]), 0.0)

    def test_add_set_copies_previous(self):
        WorkoutSet.objects.create(
            user=self.user,
            workout_exercise=self.workout_exercise,
            position=0,
            reps=12,
            weight=Decimal("80"),
            unit="kg",
        )
        response = self.client.post(
            f"/api/v1/workout-exercises/{self.workout_exercise.id}/add_set/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["reps"], 12)
        self.assertEqual(response.json()["weight"], "80.00")
        self.assertEqual(response.json()["position"], 1)

    def test_warmup_sets_do_not_count_towards_volume(self):
        WorkoutSet.objects.create(
            user=self.user,
            workout_exercise=self.workout_exercise,
            reps=10,
            weight=Decimal("20"),
            unit="kg",
            set_type="warmup",
            is_completed=True,
            completed_at=timezone.now(),
        )
        self.assertEqual(self.workout.volume, 0.0)

    def test_pounds_are_normalised_to_kg(self):
        workout_set = WorkoutSet.objects.create(
            user=self.user,
            workout_exercise=self.workout_exercise,
            reps=10,
            weight=Decimal("225"),
            unit="lb",
            is_completed=True,
            completed_at=timezone.now(),
        )
        expected = float(Decimal("225") * Decimal("0.45359237")) * 10
        self.assertAlmostEqual(workout_set.volume_kg, expected, places=1)


class AddExerciseTests(ApiTestCase):
    def test_machine_settings_are_remembered_per_location(self):
        home = GymLocation.objects.create(user=self.user, name="Home gym", kind="home")
        work = GymLocation.objects.create(user=self.user, name="Work gym", kind="work")
        exercise = self.make_exercise(name="Seated Cable Row")

        MachineSetup.objects.create(
            user=self.user,
            exercise=exercise,
            location=home,
            machine_name="Home rack",
            settings_json={"seat_height": 3, "cable_height": 6},
        )
        MachineSetup.objects.create(
            user=self.user,
            exercise=exercise,
            location=work,
            machine_name="Work tower",
            settings_json={"seat_height": 6},
        )

        workout = Workout.objects.create(
            user=self.user, location=work, started_at=timezone.now()
        )
        response = self.client.post(
            f"/api/v1/workouts/{workout.id}/exercises/",
            {"exercise": str(exercise.id), "position": 0},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["machine_name"], "Work tower")
        self.assertEqual(body["machine_settings"], {"seat_height": 6})

    def test_default_setup_falls_back_when_location_is_new(self):
        exercise = self.make_exercise(name="Lat Pulldown")
        home = GymLocation.objects.create(user=self.user, name="Home gym", kind="home")
        MachineSetup.objects.create(
            user=self.user,
            exercise=exercise,
            location=home,
            machine_name="Home lat station",
            settings_json={"knee_pad": 5},
        )
        workout = Workout.objects.create(user=self.user, started_at=timezone.now())

        response = self.client.post(
            f"/api/v1/workouts/{workout.id}/exercises/",
            {"exercise": str(exercise.id)},
            format="json",
        )
        self.assertEqual(response.json()["machine_name"], "Home lat station")

    def test_snapshot_survives_editing_the_setup(self):
        exercise = self.make_exercise(name="Leg Press")
        setup = MachineSetup.objects.create(
            user=self.user,
            exercise=exercise,
            machine_name="Original",
            settings_json={"seat": 1},
        )
        workout = Workout.objects.create(user=self.user, started_at=timezone.now())
        response = self.client.post(
            f"/api/v1/workouts/{workout.id}/exercises/",
            {"exercise": str(exercise.id)},
            format="json",
        )
        item_id = response.json()["id"]

        setup.machine_name = "Renamed next month"
        setup.settings_json = {"seat": 99}
        setup.save()

        refreshed = self.client.get(f"/api/v1/workouts/{workout.id}/").json()
        stored = next(e for e in refreshed["exercises"] if e["id"] == item_id)
        self.assertEqual(stored["machine_name"], "Original")
        self.assertEqual(stored["machine_settings"], {"seat": 1})


class TemplateTests(ApiTestCase):
    def test_instantiate_creates_workout_with_prescribed_sets(self):
        exercise = self.make_exercise(name="Back Squat", equipment="barbell")
        response = self.client.post(
            "/api/v1/templates/",
            {
                "name": "Legs day",
                "items": [
                    {
                        "exercise": str(exercise.id),
                        "position": 0,
                        "target_sets": 4,
                        "target_reps_min": 5,
                        "target_reps_max": 5,
                        "target_weight": "100.00",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        template_id = response.json()["id"]
        self.assertEqual(response.json()["item_count"], 1)

        started = self.client.post(
            f"/api/v1/templates/{template_id}/instantiate/", {}, format="json"
        )
        self.assertEqual(started.status_code, 201)
        body = started.json()
        self.assertEqual(body["display_name"], "Legs day")
        self.assertEqual(len(body["exercises"]), 1)
        sets = body["exercises"][0]["sets"]
        self.assertEqual(len(sets), 4)
        self.assertEqual(sets[0]["weight"], "100.00")
        self.assertEqual(sets[0]["reps"], 5)

    def test_duplicate_template_name_is_rejected(self):
        self.client.post("/api/v1/templates/", {"name": "PPL"}, format="json")
        response = self.client.post(
            "/api/v1/templates/", {"name": "PPL"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["error"]["details"])

    def test_target_reps_min_cannot_exceed_max(self):
        exercise = self.make_exercise()
        response = self.client.post(
            "/api/v1/templates/",
            {
                "name": "Bad",
                "items": [
                    {
                        "exercise": str(exercise.id),
                        "target_reps_min": 20,
                        "target_reps_max": 5,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_foreign_items(self):
        mine = self.make_exercise(name="Mine")
        theirs = Exercise.objects.create(user=self.other, name="Theirs")
        created = self.client.post(
            "/api/v1/templates/",
            {"name": "T", "items": [{"exercise": str(mine.id), "position": 0}]},
            format="json",
        )
        template_id = created.json()["id"]
        response = self.client.post(
            f"/api/v1/templates/{template_id}/items/reorder/",
            {"order": [str(theirs.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class LocationTests(ApiTestCase):
    def test_defaults_are_created_once(self):
        first = self.client.get("/api/v1/locations/defaults/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual({item["kind"] for item in first.json()}, {"home", "work"})
        second = self.client.get("/api/v1/locations/defaults/")
        self.assertEqual(len(second.json()), 2)

    def test_locations_are_scoped_per_user(self):
        GymLocation.objects.create(user=self.other, name="Bob's gym")
        response = self.client.get("/api/v1/locations/")
        self.assertEqual(response.data["count"], 0)

    def test_settings_json_must_be_flat(self):
        exercise = self.make_exercise()
        response = self.client.post(
            "/api/v1/machine-setups/",
            {"exercise": str(exercise.id), "settings_json": {"seat": {"nested": True}}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
