"""Project-level URL behaviour.

The root is not part of the API surface: it exists so that hitting the bare
domain lands on the versioned API root instead of a 404.
"""

from django.test import TestCase
from django.urls import reverse


class RootRedirectTests(TestCase):
    def test_root_redirects_to_the_versioned_api_root(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/api/v1/")

    def test_root_route_is_named(self):
        self.assertEqual(reverse("root"), "/")

    def test_api_root_without_trailing_slash_redirects_to_the_slashed_form(self):
        response = self.client.get("/api/v1")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/api/v1/")
