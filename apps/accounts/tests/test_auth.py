from __future__ import annotations

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.google import GoogleAuthError, GoogleIdentity, verify_google_token
from apps.accounts.models import DeviceSession, UserProfile

CLAIMS = {
    "iss": "https://accounts.google.com",
    "aud": "test-client-id.apps.googleusercontent.com",
    "azp": "test-client-id.apps.googleusercontent.com",
    "sub": "1234567890",
    "email": "Ada@example.com",
    "email_verified": True,
    "name": "Ada Lovelace",
    "picture": "https://example.com/ada.png",
}


class GoogleTokenVerificationTests(TestCase):
    def _verify(self, claims=None):
        with mock.patch(
            "apps.accounts.google._fetch_verified_claims", return_value=claims or CLAIMS
        ):
            return verify_google_token("header.payload.signature")

    @override_settings(GOOGLE_CLIENT_IDS=["test-client-id.apps.googleusercontent.com"])
    def test_accepts_valid_token(self):
        identity = self._verify()
        self.assertEqual(identity.sub, "1234567890")
        self.assertEqual(identity.email, "ada@example.com")

    @override_settings(GOOGLE_CLIENT_IDS=[""])
    def test_fails_closed_when_unconfigured(self):
        with self.assertRaises(GoogleAuthError) as ctx:
            self._verify()
        self.assertEqual(ctx.exception.code, "not_configured")

    @override_settings(GOOGLE_CLIENT_IDS=["some-other-client.apps.googleusercontent.com"])
    def test_rejects_token_for_a_different_app(self):
        with self.assertRaises(GoogleAuthError):
            self._verify()

    @override_settings(GOOGLE_CLIENT_IDS=["test-client-id.apps.googleusercontent.com"])
    def test_rejects_untrusted_issuer(self):
        claims = dict(CLAIMS, iss="https://evil.example.com")
        with self.assertRaises(GoogleAuthError):
            self._verify(claims)

    @override_settings(GOOGLE_CLIENT_IDS=["test-client-id.apps.googleusercontent.com"])
    def test_rejects_token_without_email(self):
        claims = dict(CLAIMS)
        claims.pop("email")
        with self.assertRaises(GoogleAuthError):
            self._verify(claims)

    @override_settings(GOOGLE_CLIENT_IDS=["test-client-id.apps.googleusercontent.com"])
    def test_empty_token_rejected(self):
        with self.assertRaises(GoogleAuthError):
            verify_google_token("")

    @override_settings(
        GOOGLE_CLIENT_IDS=["test-client-id.apps.googleusercontent.com"],
        GOOGLE_HD_ORG_DOMAIN="example.com",
    )
    def test_workspace_domain_restriction(self):
        with self.assertRaises(GoogleAuthError) as ctx:
            self._verify(dict(CLAIMS, hd="other.com"))
        self.assertEqual(ctx.exception.code, "wrong_domain")

        identity = self._verify(dict(CLAIMS, hd="example.com"))
        self.assertEqual(identity.sub, "1234567890")


@override_settings(GOOGLE_CLIENT_IDS=["test-client-id.apps.googleusercontent.com"])
class AuthFlowTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        patcher = mock.patch(
            "apps.accounts.google._fetch_verified_claims", return_value=CLAIMS
        )
        self.mock_claims = patcher.start()
        self.addCleanup(patcher.stop)

    def sign_in(self, **extra):
        payload = {"id_token": "header.payload.signature"}
        payload.update(extra)
        return self.client.post("/api/v1/auth/google/", payload, format="json")

    def test_first_sign_in_creates_the_user(self):
        response = self.sign_in(device_name="Pixel 9", platform="android")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["is_new_user"])
        self.assertIn("access", body)
        self.assertIn("refresh", body)
        self.assertEqual(body["user"]["email"], "ada@example.com")

        user = User.objects.get(email="ada@example.com")
        self.assertEqual(user.profile.google_sub, "1234567890")
        self.assertFalse(user.has_usable_password())

    def test_repeat_sign_in_reuses_the_account_and_adds_a_session(self):
        self.sign_in(device_name="Pixel 9")
        second = self.sign_in(device_name="iPhone")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["is_new_user"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(DeviceSession.objects.filter(user__email="ada@example.com").count(), 2)

    def test_display_name_prefers_the_google_name(self):
        response = self.sign_in()
        self.assertEqual(response.json()["user"]["display_name"], "Ada Lovelace")

    def test_display_name_falls_back_to_the_email(self):
        self.mock_claims.return_value = dict(CLAIMS, name="")
        response = self.sign_in()
        self.assertEqual(response.json()["user"]["display_name"], "ada")

    def test_invalid_token_is_unauthorized(self):
        self.mock_claims.side_effect = ValueError("bad signature")
        response = self.sign_in()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_token")
        self.assertEqual(User.objects.count(), 0)

    def test_missing_token_field_is_rejected(self):
        response = self.client.post("/api/v1/auth/google/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_refresh_rotates_the_token(self):
        refresh = self.sign_in().json()["refresh"]
        response = self.client.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 200)
        rotated = response.json()["refresh"]
        self.assertNotEqual(refresh, rotated)

        old = self.client.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(old.status_code, 401)
        self.assertEqual(old.json()["error"]["code"], "session_revoked")

    def test_garbage_refresh_token_is_rejected(self):
        response = self.client.post("/api/v1/auth/refresh/", {"refresh": "nope"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_access_token_authenticates_the_api(self):
        access = self.sign_in().json()["access"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(client.get("/api/v1/auth/me/").status_code, 200)
        self.assertEqual(client.get("/api/v1/auth/me/").json()["email"], "ada@example.com")

    def test_me_patch_updates_preferences(self):
        access = self.sign_in().json()["access"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = client.patch("/api/v1/auth/me/", {"units": "lb"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["units"], "lb")

    def test_logout_revokes_all_sessions(self):
        first = self.sign_in().json()["access"]
        self.sign_in()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {first}")
        self.assertEqual(client.post("/api/v1/auth/logout/", {}, format="json").status_code, 204)
        self.assertEqual(DeviceSession.objects.filter(revoked_at__isnull=True).count(), 0)

        refresh_client = APIClient()
        stale = DeviceSession.objects.first()
        self.assertIsNotNone(stale.revoked_at)

    def test_me_requires_authentication(self):
        self.assertEqual(APIClient().get("/api/v1/auth/me/").status_code, 401)


@override_settings(GOOGLE_CLIENT_IDS=["test-client-id.apps.googleusercontent.com"])
class ProfileTests(TestCase):
    def test_profile_is_created_with_the_user(self):
        user = User.objects.create_user(username="ada", email="ada@example.com")
        self.assertIsInstance(user.profile, UserProfile)
        self.assertEqual(user.profile.units, "kg")