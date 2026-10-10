from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.workouts.models import Exercise, GymLocation, Workout, WorkoutSet

PUSH = "/api/v1/sync/push/"
PULL = "/api/v1/sync/pull/"


class SyncTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(username="ada", email="ada@example.com")
        self.other = User.objects.create_user(username="bob", email="bob@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def push(self, ops):
        return self.client.post(PUSH, {"ops": ops}, format="json")

    def op(self, type_name, data, *, op_id=None, base_rev=0, action="upsert"):
        return {
            "type": type_name,
            "id": str(op_id or uuid.uuid4()),
            "action": action,
            "base_rev": base_rev,
            "data": data,
        }


class PushTests(SyncTestCase):
    def test_creates_with_client_supplied_uuid(self):
        chosen = uuid.uuid4()
        response = self.push(
            [self.op("location", {"name": "Home gym", "kind": "home"}, op_id=chosen)]
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["applied"], 1)
        self.assertEqual(response.json()["results"][0]["status"], "applied")
        self.assertTrue(GymLocation.objects.filter(pk=chosen).exists())

    def test_update_with_correct_base_rev_applies(self):
        location = GymLocation.objects.create(user=self.user, name="Gym")
        response = self.push(
            [
                self.op(
                    "location",
                    {"name": "Renamed"},
                    op_id=location.id,
                    base_rev=location.rev,
                )
            ]
        )
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "applied")
        self.assertGreater(result["rev"], location.rev)
        location.refresh_from_db()
        self.assertEqual(location.name, "Renamed")

    def test_stale_base_rev_reports_conflict_and_keeps_server_data(self):
        location = GymLocation.objects.create(user=self.user, name="Gym")
        stale_rev = location.rev - 1
        response = self.push(
            [
                self.op(
                    "location",
                    {"name": "Clobbered"},
                    op_id=location.id,
                    base_rev=stale_rev,
                )
            ]
        )
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["data"]["name"], "Gym")
        location.refresh_from_db()
        self.assertEqual(location.name, "Gym")

    def test_replaying_the_same_push_is_idempotent(self):
        chosen = uuid.uuid4()
        ops = [self.op("location", {"name": "Home gym", "kind": "home"}, op_id=chosen)]
        first = self.push(ops)
        second = self.push(ops)
        # Second attempt has base_rev 0 against a row now at rev 2 -> conflict,
        # but critically no duplicate row is created.
        self.assertEqual(second.json()["results"][0]["status"], "conflict")
        self.assertEqual(GymLocation.objects.filter(pk=chosen).count(), 1)
        self.assertEqual(first.json()["applied"], 1)

    def test_delete_is_soft_and_idempotent(self):
        location = GymLocation.objects.create(user=self.user, name="Gym")
        response = self.push(
            [
                self.op(
                    "location",
                    {},
                    op_id=location.id,
                    base_rev=location.rev,
                    action="delete",
                )
            ]
        )
        self.assertEqual(response.json()["results"][0]["status"], "applied")
        location.refresh_from_db()
        self.assertIsNotNone(location.deleted_at)

        again = self.push(
            [
                self.op(
                    "location",
                    {},
                    op_id=location.id,
                    base_rev=location.rev,
                    action="delete",
                )
            ]
        )
        self.assertEqual(again.json()["results"][0]["status"], "applied")
        self.assertEqual(GymLocation.objects.filter(pk=location.id).count(), 1)

    def test_cannot_write_to_another_users_row(self):
        victim = GymLocation.objects.create(user=self.other, name="Bob's gym")
        response = self.push(
            [
                self.op(
                    "location", {"name": "Stolen"}, op_id=victim.id, base_rev=victim.rev
                )
            ]
        )
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "rejected")
        victim.refresh_from_db()
        self.assertEqual(victim.name, "Bob's gym")

    def test_cannot_attach_child_to_another_users_parent(self):
        victim_workout = Workout.objects.create(
            user=self.other, started_at=timezone.now()
        )
        response = self.push(
            [
                self.op(
                    "workout_exercise",
                    {
                        "workout": str(victim_workout.id),
                        "exercise_name": "Squat",
                        "position": 0,
                    },
                )
            ]
        )
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "rejected")
        self.assertIn("workout", result["error"])

    def test_client_cannot_forge_the_user_field(self):
        chosen = uuid.uuid4()
        self.push(
            [
                self.op(
                    "location",
                    {"name": "X", "kind": "gym", "user": str(self.other.id)},
                    op_id=chosen,
                )
            ]
        )
        self.assertEqual(GymLocation.objects.get(pk=chosen).user_id, self.user.id)

    def test_unknown_type_is_rejected_without_failing_the_batch(self):
        good = self.op("location", {"name": "Fine", "kind": "gym"})
        response = self.push([self.op("nonsense", {"name": "?"}), good])
        body = response.json()
        self.assertEqual(body["applied"], 1)
        statuses = {item["status"] for item in body["results"]}
        self.assertEqual(statuses, {"rejected", "applied"})

    def test_invalid_data_is_rejected_without_poisoning_the_batch(self):
        bad = self.op("location", {"name": "", "kind": "gym"})
        good = self.op("exercise", {"name": "Squat", "equipment": "barbell"})
        response = self.push([bad, good])
        self.assertEqual(response.json()["applied"], 1)
        self.assertEqual(Exercise.objects.filter(user=self.user).count(), 1)

    def test_snapshot_fields_are_filled_from_the_exercise(self):
        exercise = self.make_exercise()
        workout = Workout.objects.create(user=self.user, started_at=timezone.now())
        response = self.push(
            [
                self.op(
                    "workout_exercise",
                    {
                        "workout": str(workout.id),
                        "exercise": str(exercise.id),
                        "position": 0,
                    },
                )
            ]
        )
        self.assertEqual(response.json()["results"][0]["status"], "applied")
        stored = workout.exercises.get()
        self.assertEqual(stored.exercise_name, exercise.name)
        self.assertEqual(stored.muscle_group, exercise.muscle_group)
        self.assertEqual(stored.equipment, exercise.equipment)

    def test_requires_authentication(self):
        anon = APIClient()
        response = anon.post(PUSH, {"ops": []}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_payload_validation(self):
        self.assertEqual(
            self.client.post(PUSH, {"ops": "nope"}, format="json").status_code, 400
        )

    def test_batch_size_is_capped(self):
        ops = [
            self.op("location", {"name": f"L{i}", "kind": "gym"}) for i in range(501)
        ]
        self.assertEqual(self.push(ops).status_code, 413)

    def make_exercise(self):
        return Exercise.objects.create(
            user=self.user, name="Leg Press", equipment="machine"
        )


class PullTests(SyncTestCase):
    def test_cursor_only_returns_newer_rows(self):
        location = GymLocation.objects.create(user=self.user, name="Gym")
        first = self.client.get(f"{PULL}?since=0").json()
        self.assertEqual(first["cursor"], location.seq)
        self.assertIn("location", first["changes"])
        self.assertEqual(first["has_more"], False)

        second = self.client.get(f"{PULL}?since={first['cursor']}").json()
        self.assertEqual(second["changes"], {})

    def test_deleted_rows_are_pushed_as_tombstones(self):
        location = GymLocation.objects.create(user=self.user, name="Gym")
        cursor = location.seq
        location.soft_delete(self.user.id)

        body = self.client.get(f"{PULL}?since={cursor}").json()
        tombstone = body["changes"]["location"][0]
        self.assertIsNotNone(tombstone["deleted_at"])

    def test_pull_never_leaks_another_users_rows(self):
        GymLocation.objects.create(user=self.other, name="Bob's gym")
        body = self.client.get(f"{PULL}?since=0").json()
        self.assertEqual(body["changes"], {})

    def test_invalid_cursor_is_a_client_error(self):
        self.assertEqual(self.client.get(f"{PULL}?since=abc").status_code, 400)

    def test_limit_produces_a_continuation_cursor(self):
        for index in range(5):
            GymLocation.objects.create(user=self.user, name=f"Gym {index}")
        body = self.client.get(f"{PULL}?since=0&limit=2").json()
        self.assertTrue(body["has_more"])
        self.assertEqual(len(body["changes"]["location"]), 2)

        page_two = self.client.get(f"{PULL}?since={body['cursor']}&limit=10").json()
        seen = {item["name"] for item in body["changes"]["location"]} | {
            item["name"] for item in page_two["changes"]["location"]
        }
        self.assertEqual(len(seen), 5)

    def test_round_trip_push_then_pull(self):
        chosen = uuid.uuid4()
        self.push(
            [self.op("location", {"name": "Offline gym", "kind": "work"}, op_id=chosen)]
        )
        body = self.client.get(f"{PULL}?since=0").json()
        stored = body["changes"]["location"][0]
        self.assertEqual(stored["id"], str(chosen))
        self.assertEqual(stored["name"], "Offline gym")
        self.assertEqual(stored["rev"], 1)


class ConcurrentSequenceTests(SyncTestCase):
    def test_sequence_numbers_are_unique_and_monotonic(self):
        ids = []
        for index in range(10):
            ids.append(GymLocation.objects.create(user=self.user, name=f"G{index}").seq)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids, sorted(ids))

    def test_sequences_are_per_user(self):
        mine = GymLocation.objects.create(user=self.user, name="Mine").seq
        theirs = GymLocation.objects.create(user=self.other, name="Theirs").seq
        self.assertEqual(mine, 1)
        self.assertEqual(theirs, 1)
