"""Guards for structural mistakes that unit tests happily paper over.

The app registry creates tables for models that have no migration when it sets
up a test database, so a missing migrations module passes every unit test and
then fails in production with ``relation "..." does not exist``. These tests
make that loud in CI instead.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase


class MigrationTests(TestCase):
    """Needs a real database: ``makemigrations --check`` consults the recorder."""

    def test_no_models_are_missing_migrations(self):
        """Every model must be backed by a real migration file."""
        output = StringIO()
        try:
            call_command(
                "makemigrations", "--check", "--dry-run", stdout=output, stderr=output, verbosity=0
            )
        except SystemExit as exc:  # pragma: no cover - only on failure
            self.fail(f"Models changed without a migration:\n{output.getvalue()}\n{exc}")

    def test_every_installed_app_has_migrations(self):
        """An app in INSTALLED_APPS with no migrations package is a silent trap."""
        from django.apps import apps

        missing = [
            app.label
            for app in apps.get_app_configs()
            if not app.name.startswith("django.")
            and not app.name.startswith("rest_framework")
            and app.name not in {"corsheaders", "django_filters"}
            and not _has_migrations(app)
        ]
        self.assertEqual(missing, [], f"Apps without migrations: {missing}")


def _has_migrations(app_config) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(f"{app_config.name}.migrations") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


class SequenceTests(TestCase):
    def test_sync_state_table_exists(self):
        tables = set(connection.introspection.table_names())
        self.assertIn("core_syncstate", tables)

    def test_sequence_allocates_for_a_fresh_user(self):
        from django.contrib.auth.models import User

        from apps.core.models import SyncState, next_sequence

        user = User.objects.create_user(username="seq-tester")
        self.assertEqual(next_sequence(user.id), 1)
        self.assertEqual(next_sequence(user.id), 2)
        self.assertTrue(SyncState.objects.filter(user=user).exists())

    def test_sequence_is_isolated_per_user(self):
        from django.contrib.auth.models import User

        from apps.core.models import next_sequence

        first = User.objects.create_user(username="a")
        second = User.objects.create_user(username="b")
        next_sequence(first.id)
        self.assertEqual(next_sequence(second.id), 1)