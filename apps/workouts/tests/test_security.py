"""Regression tests for the security fixes.

Each test here corresponds to a specific finding: if one of these starts failing,
the corresponding vulnerability has come back.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import DeviceSession, UserProfile
from apps.workouts.models import (
    Exercise,
    GymLocation,
    MachineSetup,
    TemplateItem,
    Workout,
    WorkoutExercise,
    WorkoutSet,
    WorkoutTemplate,
)


class TenantTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ada = User.objects.create_user(username="ada", email="ada@example.com")
        self.bob = User.objects.create_user(username="bob", email="bob@example.com")

        self.ada_api = APIClient()
        self.ada_api.force_authenticate(self.ada)
        self.bob_api = APIClient()
        self.bob_api.force_authenticate(self.bob)

        # Bob's private data.
        self.bob_location = GymLocation.objects.create(
            user=self.bob, name="Bob Secret Gym"
        )
        self.bob_exercise = Exercise.objects.create(
            user=self.bob,
            name="Bob Secret Lift",
            muscle_group="back",
            equipment="cable",
        )
        self.bob_workout = Workout.objects.create(
            user=self.bob, started_at=timezone.now()
        )
        self.bob_workout_exercise = WorkoutExercise.objects.create(
            user=self.bob,
            workout=self.bob_workout,
            exercise=self.bob_exercise,
            exercise_name=self.bob_exercise.name,
            muscle_group=self.bob_exercise.muscle_group,
            equipment=self.bob_exercise.equipment,
        )
        self.bob_set = WorkoutSet.objects.create(
            user=self.bob,
            workout_exercise=self.bob_workout_exercise,
            reps=5,
            weight=Decimal("100"),
            unit="kg",
            is_completed=True,
            completed_at=timezone.now(),
        )


class CrossTenantWriteTests(TenantTestCase):
    """H1: related fields must never resolve to another athlete's rows."""

    def test_cannot_attach_a_set_to_another_users_exercise(self):
        response = self.ada_api.post(
            "/api/v1/sets/",
            {
                "workout_exercise": str(self.bob_workout_exercise.id),
                "reps": 999,
                "weight": "500",
                "is_completed": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.bob_set.refresh_from_db()
        self.assertEqual(self.bob_set.reps, 5)

    def test_injected_set_does_not_inflate_the_victims_volume(self):
        self.ada_api.post(
            "/api/v1/sets/",
            {
                "workout_exercise": str(self.bob_workout_exercise.id),
                "reps": 999,
                "weight": "500",
            },
            format="json",
        )
        self.bob_workout.refresh_from_db()
        self.assertEqual(self.bob_workout.volume, 500.0)

    def test_cannot_attach_a_workout_exercise_to_another_users_workout(self):
        response = self.ada_api.post(
            "/api/v1/workout-exercises/",
            {
                "workout": str(self.bob_workout.id),
                "exercise": str(self.bob_exercise.id),
                "exercise_name": "Bob Secret Lift",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.bob_workout.exercises.count(), 1)

    def test_machine_setup_cannot_reference_another_users_exercise(self):
        response = self.ada_api.post(
            "/api/v1/machine-setups/",
            {
                "exercise": str(self.bob_exercise.id),
                "location": str(self.bob_location.id),
                "machine_name": "Stolen",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Bob Secret", str(response.json()))

    def test_template_cannot_reference_another_users_location(self):
        response = self.ada_api.post(
            "/api/v1/templates/",
            {"name": "Theft", "default_location": str(self.bob_location.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_template_item_cannot_reference_another_users_exercise(self):
        template = WorkoutTemplate.objects.create(user=self.ada, name="Mine")
        response = self.ada_api.post(
            "/api/v1/templates/",
            {"name": "Theft", "items": [{"exercise": str(self.bob_exercise.id)}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TemplateItem.objects.filter(template=template).count(), 0)

    def test_profile_cannot_point_at_another_users_location(self):
        response = self.ada_api.patch(
            "/api/v1/auth/me/",
            {"default_location": str(self.bob_location.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_read_another_users_rows(self):
        for url in (
            f"/api/v1/workouts/{self.bob_workout.id}/",
            f"/api/v1/workout-exercises/{self.bob_workout_exercise.id}/",
            f"/api/v1/sets/{self.bob_set.id}/",
        ):
            self.assertEqual(self.ada_api.get(url).status_code, 404, url)

    def test_cannot_finish_or_complete_another_users_records(self):
        self.assertEqual(
            self.ada_api.post(
                f"/api/v1/workouts/{self.bob_workout.id}/finish/", {}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.ada_api.post(
                f"/api/v1/sets/{self.bob_set.id}/complete/",
                {"is_completed": True},
                format="json",
            ).status_code,
            404,
        )

    def test_start_workout_ignores_a_foreign_location_id(self):
        response = self.ada_api.post(
            "/api/v1/workouts/start/",
            {"location": str(self.bob_location.id)},
            format="json",
        )
        self.assertIn(response.status_code, (200, 201))
        workout_id = response.json()["id"]
        workout = Workout.objects.get(pk=workout_id)
        self.assertNotEqual(str(workout.location_id), str(self.bob_location.id))


class CursorIntegrityTests(TenantTestCase):
    """H5: the cursor must never advance past rows that were not delivered."""

    def test_truncated_pull_does_not_skip_remaining_rows(self):
        for _ in range(5):
            Workout.objects.create(user=self.ada, started_at=timezone.now())

        first = self.ada_api.get("/api/v1/sync/pull/?since=0&limit=2").json()
        self.assertTrue(first["has_more"])

        seen = {item["id"] for item in first["changes"].get("workout", [])}
        cursor = first["cursor"]
        guard = 0
        while guard < 20:
            guard += 1
            page = self.ada_api.get(f"/api/v1/sync/pull/?since={cursor}&limit=2").json()
            seen |= {item["id"] for item in page["changes"].get("workout", [])}
            cursor = page["cursor"]
            if not page["has_more"]:
                break

        self.assertEqual(len(seen), 5, "a truncated pull silently dropped rows")

    def test_cursor_is_stable_once_fully_drained(self):
        Workout.objects.create(user=self.ada, started_at=timezone.now())
        first = self.ada_api.get("/api/v1/sync/pull/?since=0&limit=100").json()
        self.assertFalse(first["has_more"])
        second = self.ada_api.get(
            f"/api/v1/sync/pull/?since={first['cursor']}&limit=100"
        ).json()
        self.assertEqual(second["changes"], {})
        self.assertEqual(second["cursor"], first["cursor"])


GOOGLE_CLIENT_ID = "test-client.apps.googleusercontent.com"


@override_settings(GOOGLE_CLIENT_IDS=[GOOGLE_CLIENT_ID])
class GoogleIdentityTests(TestCase):
    def _signin(self, claims):
        from unittest import mock

        patcher = mock.patch(
            "apps.accounts.google._fetch_verified_claims", return_value=claims
        )
        token_mock = patcher.start()
        self.addCleanup(patcher.stop)
        return APIClient(), token_mock

    def test_unverified_email_is_refused(self):
        base = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_CLIENT_ID,
            "sub": "sub-1",
            "email": "ada@example.com",
            "email_verified": False,
            "name": "Ada",
        }
        api, _ = self._signin(base)
        response = api.post("/api/v1/auth/google/", {"id_token": "x"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unverified_email")
        self.assertEqual(User.objects.count(), 0)

    def test_google_subject_cannot_be_rebound(self):
        first = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_CLIENT_ID,
            "sub": "sub-ORIGINAL",
            "email": "ada@example.com",
            "email_verified": True,
            "name": "Ada",
        }
        api, patcher = self._signin(first)
        self.assertEqual(
            api.post(
                "/api/v1/auth/google/", {"id_token": "x"}, format="json"
            ).status_code,
            201,
        )

        attacker = dict(first, sub="sub-ATTACKER", name="Mallory")
        patcher.return_value = attacker
        response = api.post("/api/v1/auth/google/", {"id_token": "x"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "identity_conflict")

        profile = UserProfile.objects.get(user__email="ada@example.com")
        self.assertEqual(profile.google_sub, "sub-ORIGINAL")
        self.assertEqual(User.objects.count(), 1)


@override_settings(GOOGLE_CLIENT_IDS=[GOOGLE_CLIENT_ID])
class SessionRevocationTests(TestCase):
    def setUp(self):
        super().setUp()
        from unittest import mock

        self.claims = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_CLIENT_ID,
            "sub": "sub-1",
            "email": "ada@example.com",
            "email_verified": True,
            "name": "Ada",
        }
        patcher = mock.patch(
            "apps.accounts.google._fetch_verified_claims", return_value=self.claims
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.api = APIClient()
        body = self.api.post(
            "/api/v1/auth/google/", {"id_token": "x"}, format="json"
        ).json()
        self.access = body["access"]
        self.user = User.objects.get(email="ada@example.com")

    def authed(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        return client

    def test_access_token_works_before_logout(self):
        self.assertEqual(self.authed().get("/api/v1/auth/me/").status_code, 200)

    def test_logout_invalidates_the_live_access_token(self):
        self.assertEqual(
            self.authed().post("/api/v1/auth/logout/", {}, format="json").status_code,
            204,
        )
        response = self.authed().get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)

    def test_revoking_one_device_kills_its_access_token(self):
        other = self.api.post(
            "/api/v1/auth/google/",
            {"id_token": "x", "device_name": "other"},
            format="json",
        ).json()
        session_id = other["session_id"]

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {other['access']}")
        self.assertEqual(client.get("/api/v1/auth/me/").status_code, 200)

        self.assertEqual(
            self.authed()
            .post(f"/api/v1/auth/devices/{session_id}/revoke/", {}, format="json")
            .status_code,
            204,
        )
        self.assertEqual(client.get("/api/v1/auth/me/").status_code, 401)

    def test_refresh_after_logout_is_refused(self):
        refresh = self.api.post(
            "/api/v1/auth/google/", {"id_token": "x"}, format="json"
        ).json()["refresh"]
        self.authed().post("/api/v1/auth/logout/", {}, format="json")
        response = self.api.post(
            "/api/v1/auth/refresh/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(response.status_code, 401)


@override_settings(GOOGLE_CLIENT_IDS=[GOOGLE_CLIENT_ID])
class PasswordChangeTests(TestCase):
    def setUp(self):
        super().setUp()
        from unittest import mock

        self.claims = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_CLIENT_ID,
            "sub": "sub-1",
            "email": "ada@example.com",
            "email_verified": True,
            "name": "Ada",
        }
        patcher = mock.patch(
            "apps.accounts.google._fetch_verified_claims", return_value=self.claims
        )
        self.patcher = patcher.start()
        self.addCleanup(patcher.stop)

        self.api = APIClient()
        body = self.api.post(
            "/api/v1/auth/google/", {"id_token": "x"}, format="json"
        ).json()
        self.access = body["access"]
        self.user = User.objects.get(email="ada@example.com")

    def authed(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        return client

    def test_google_only_account_cannot_set_a_password_with_a_bogus_current_one(self):
        self.assertFalse(self.user.has_usable_password())
        response = self.authed().post(
            "/api/v1/auth/password/",
            {"current_password": "whatever", "new_password": "attacker-owned-123"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_reauth_with_the_matching_google_token_is_accepted(self):
        response = self.authed().post(
            "/api/v1/auth/password/",
            {"google_id_token": "fresh", "new_password": "a-good-password-1"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())

    def test_reauth_with_a_different_google_identity_is_refused(self):
        self.patcher.return_value = dict(
            self.claims, sub="sub-someone-else", email="other@example.com"
        )
        response = self.authed().post(
            "/api/v1/auth/password/",
            {"google_id_token": "fresh", "new_password": "a-good-password-1"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_weak_passwords_are_rejected_by_the_validators(self):
        response = self.authed().post(
            "/api/v1/auth/password/",
            {"google_id_token": "fresh", "new_password": "password"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class ConfigurationTests(TestCase):
    def test_signin_fails_closed_without_google_client_ids(self):
        from unittest import mock

        api = APIClient()
        with override_settings(GOOGLE_CLIENT_IDS=[]):
            with mock.patch(
                "apps.accounts.google._fetch_verified_claims",
                return_value={"sub": "s", "email": "a@e.com"},
            ):
                response = api.post(
                    "/api/v1/auth/google/", {"id_token": "x"}, format="json"
                )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_configured")


class BulkUpdateTests(TestCase):
    """M1: writes that bypass save() must still advance rev/seq."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="ada", email="ada@example.com")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_reordering_bumps_the_revision(self):
        exercise = Exercise.objects.create(user=self.user, name="Squat")
        template = WorkoutTemplate.objects.create(user=self.user, name="Legs")
        first = TemplateItem.objects.create(
            user=self.user, template=template, exercise=exercise, position=0
        )
        second = TemplateItem.objects.create(
            user=self.user, template=template, exercise=exercise, position=1
        )

        before = first.rev
        response = self.api.post(
            f"/api/v1/templates/{template.id}/items/reorder/",
            {"order": [str(second.id), str(first.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        first.refresh_from_db()
        self.assertEqual(first.position, 1)
        self.assertGreater(first.rev, before)

    def test_reordering_rejects_a_foreign_item(self):
        exercise = Exercise.objects.create(user=self.user, name="Squat")
        template = WorkoutTemplate.objects.create(user=self.user, name="Legs")
        other = User.objects.create_user(username="bob", email="bob@example.com")
        foreign = TemplateItem.objects.create(
            user=other, template=template, exercise=exercise
        )
        response = self.api.post(
            f"/api/v1/templates/{template.id}/items/reorder/",
            {"order": [str(foreign.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_workout_exercise_reorder_works(self):
        workout = Workout.objects.create(user=self.user, started_at=timezone.now())
        a = WorkoutExercise.objects.create(
            user=self.user, workout=workout, exercise_name="A", position=0
        )
        b = WorkoutExercise.objects.create(
            user=self.user, workout=workout, exercise_name="B", position=1
        )
        response = self.api.post(
            f"/api/v1/workouts/{workout.id}/exercises/reorder/",
            {"order": [str(b.id), str(a.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 204, response.content)
        a.refresh_from_db()
        self.assertEqual(a.position, 1)


class InputHardeningTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="ada", email="ada@example.com")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.workout = Workout.objects.create(
            user=self.user,
            started_at=timezone.now() - timedelta(hours=2),
            finished_at=timezone.now(),
        )
        self.exercise = Exercise.objects.create(user=self.user, name="Leg Press")
        self.we = WorkoutExercise.objects.create(
            user=self.user,
            workout=self.workout,
            exercise=self.exercise,
            exercise_name="Leg Press",
        )
        self.workout_set = WorkoutSet.objects.create(
            user=self.user,
            workout_exercise=self.we,
            reps=10,
            weight=Decimal("100"),
            unit="kg",
        )

    def test_target_sets_is_capped(self):
        response = self.api.post(
            f"/api/v1/workouts/{self.workout.id}/exercises/",
            {"exercise": str(self.exercise.id), "target_sets": 20000},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertLessEqual(response.json()["sets"].__len__(), 30)

    def test_non_numeric_weight_is_a_400_not_a_500(self):
        response = self.api.post(
            f"/api/v1/sets/{self.workout_set.id}/complete/",
            {"is_completed": True, "weight": "not-a-number"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_out_of_range_rpe_is_rejected(self):
        response = self.api.post(
            f"/api/v1/sets/{self.workout_set.id}/complete/",
            {"is_completed": True, "rpe": "99"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_non_numeric_reps_does_not_crash(self):
        response = self.api.post(
            f"/api/v1/sets/{self.workout_set.id}/complete/",
            {"is_completed": True, "reps": "abc"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.workout_set.refresh_from_db()
        self.assertEqual(self.workout_set.reps, 10)

    def test_junk_dashboard_query_params_do_not_500(self):
        for url in (
            "/api/v1/dashboard/summary/?days=abc",
            "/api/v1/dashboard/weekly-volume/?weeks=abc",
            "/api/v1/dashboard/personal-records/?limit=abc",
            "/api/v1/dashboard/volume-by-muscle/?days=abc",
        ):
            self.assertEqual(self.api.get(url).status_code, 200, url)

    def test_unknown_unit_falls_back_to_kg(self):
        response = self.api.post(
            "/api/v1/workouts/start/", {"unit": "stone"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Workout.objects.get(pk=response.json()["id"]).unit, "kg")

    def test_sync_push_does_not_leak_internal_exception_text(self):
        response = self.api.post(
            "/api/v1/sync/push/",
            {"ops": [{"type": "location", "id": "not-a-uuid", "data": {"name": "x"}}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        error = response.json()["results"][0].get("error", "")
        self.assertNotIn("Traceback", error)
        self.assertNotIn("psycopg", error)
        self.assertNotIn("SELECT", error.upper())


class SequenceRaceTests(TestCase):
    def test_first_sequence_allocation_is_created_once(self):
        user = User.objects.create_user(username="race")
        from apps.core.models import SyncState, next_sequence

        self.assertEqual(next_sequence(user.id), 1)
        self.assertEqual(SyncState.objects.filter(user=user).count(), 1)
        self.assertEqual(next_sequence(user.id), 2)


class ThrottlingTests(TestCase):
    """M2: the sign-in endpoint must resist credential-stuffing style abuse."""

    @override_settings(GOOGLE_CLIENT_IDS=[GOOGLE_CLIENT_ID])
    def test_repeated_google_signins_are_rate_limited(self):
        from unittest import mock

        from django.core.cache import cache
        from rest_framework.throttling import ScopedRateThrottle

        from apps.accounts.views import AuthViewSet

        claims = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_CLIENT_ID,
            "sub": "sub-throttle",
            "email": "ada@example.com",
            "email_verified": True,
            "name": "Ada",
        }

        # DRF snapshots DEFAULT_THROTTLE_CLASSES / DEFAULT_THROTTLE_RATES onto
        # the classes at import time, and the test settings deliberately leave
        # throttling off. Patch both attributes to exercise the real code path.
        cache.clear()
        api = APIClient()
        with mock.patch(
            "apps.accounts.google._fetch_verified_claims", return_value=claims
        ):
            with mock.patch.dict(
                ScopedRateThrottle.THROTTLE_RATES, {"google_signin": "3/min"}
            ):
                with mock.patch.object(
                    AuthViewSet, "throttle_classes", [ScopedRateThrottle]
                ):
                    codes = [
                        api.post(
                            "/api/v1/auth/google/", {"id_token": "x"}, format="json"
                        ).status_code
                        for _ in range(6)
                    ]
        cache.clear()

        self.assertEqual(codes[:3], [201, 200, 200])
        self.assertIn(429, codes, f"sign-in was never throttled: {codes}")

    def test_auth_endpoints_declare_throttle_scopes(self):
        from apps.accounts.views import AuthViewSet
        from apps.workouts.views import SyncViewSet

        # @action stashes its per-action kwargs on the decorated function.
        self.assertEqual(AuthViewSet.google.kwargs["throttle_scope"], "google_signin")
        self.assertEqual(AuthViewSet.refresh.kwargs["throttle_scope"], "token_refresh")
        self.assertEqual(SyncViewSet.push.kwargs["throttle_scope"], "sync_push")

    def test_declared_throttle_scopes_reach_the_routed_views(self):
        """Only a router copies @action kwargs onto the view, so wiring these
        endpoints with a hand-written as_view() silently drops them."""
        from django.urls import resolve

        expected = {
            "/api/v1/auth/google/": "google_signin",
            "/api/v1/auth/refresh/": "token_refresh",
            "/api/v1/sync/push/": "sync_push",
        }
        for path, scope in expected.items():
            with self.subTest(path=path):
                view = resolve(path).func
                self.assertEqual(view.initkwargs.get("throttle_scope"), scope)

    def test_configured_rates_are_well_formed(self):
        from django.conf import settings

        for scope, rate in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"].items():
            self.assertIn("/", rate, f"throttle rate '{scope}' must look like '60/min'")


class ProxyHeaderTests(TestCase):
    """TLS terminates upstream, so the SSL redirect must follow X-Forwarded-Proto.

    Both Cloudflare Tunnel and Nginx Proxy Manager set `X-Forwarded-Proto: https`
    when forwarding to the origin.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="ada", email="ada@example.com")

    def test_trust_forwarded_proto_header_is_configured(self):
        from django.conf import settings

        self.assertEqual(
            settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
        )

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_https_via_forwarded_proto_is_served(self):
        client = APIClient()
        client.force_authenticate(self.user)
        client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_plaintext_request_is_redirected(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response.headers["Location"].startswith("https://"))

    def test_redirect_is_off_in_the_development_configuration(self):
        # config/test_settings.py mirrors the dev .env. Note Django's test
        # runner forces DEBUG=False itself, so assert on the redirect setting
        # rather than on DEBUG.
        from django.conf import settings

        self.assertFalse(settings.SECURE_SSL_REDIRECT)

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_redirect_off_allows_plain_local_traffic(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get("/healthz").status_code, 200)
