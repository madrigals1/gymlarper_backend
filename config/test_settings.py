"""Test settings.

Runs against SQLite so the suite is fast and dependency-free. Production uses
Postgres; the models avoid Postgres-only features, so behaviour matches.
"""

from config.settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Keep hashing cheap in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

GOOGLE_CLIENT_IDS = ["test-client-id.apps.googleusercontent.com"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": [],
    "root": {"handlers": [], "level": "CRITICAL"},
}

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Rate limiting uses a process-wide cache, so a suite that signs in dozens of
# times would trip the anon/signin limits and fail for the wrong reason.
# Throttling itself is covered by a dedicated test with a tight rate.
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []
