"""Every environment value the project reads, parsed once at import time.

`.env.example` is the documented list; this module is its typed Python mirror -
same variable names, so the two can be read side by side. Keeping the parsing
here means `settings.py` only has to map values onto Django's setting names, and
anything else that needs configuration (management commands, scripts, tests)
can import from here directly:

    from config.env import POSTGRES_HOST

Real environment variables always win over `.env`, so `docker compose` can
override the file wholesale via `env_file`. `.env` is optional: production
containers get their values from the environment and this reads nothing.

`.env` holds local development values and must never be committed.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Never overrides an already-exported variable, so the environment wins over
# the file - which is what lets a container's env_file beat a baked-in `.env`.
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str:
    """Read a string. An empty value counts as unset, matching `.env` habits."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default if default is not None else ""
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int = 0) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{name} must be an integer, got {os.environ.get(name)!r}."
        ) from exc


def env_list(name: str, default: str = "") -> list[str]:
    raw = env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Core --------------------------------------------------------------------

DJANGO_DEBUG = env_bool("DJANGO_DEBUG", False)

# A known default key means anyone can mint a valid access token, so refuse to
# start without an explicit one whenever DEBUG is off.
DJANGO_SECRET_KEY = env("DJANGO_SECRET_KEY")
if not DJANGO_SECRET_KEY:
    if not DJANGO_DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false. "
            "Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    DJANGO_SECRET_KEY = "insecure-dev-only-key"

DJANGO_ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
DJANGO_CORS_ALLOWED_ORIGINS = env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8080"
)
# Empty means "fall back to the CORS origins", which is applied in settings.
DJANGO_CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
# Public origin used to build absolute URLs; stored without a trailing slash.
DJANGO_PUBLIC_ORIGIN = env("DJANGO_PUBLIC_ORIGIN", "http://localhost:8000").rstrip("/")
DJANGO_TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")

# --- Database ----------------------------------------------------------------

DJANGO_USE_SQLITE = env_bool("DJANGO_USE_SQLITE", False)
POSTGRES_DB = env("POSTGRES_DB", "gymlarper")
POSTGRES_USER = env("POSTGRES_USER", "gymlarper")
POSTGRES_PASSWORD = env("POSTGRES_PASSWORD", "")
POSTGRES_HOST = env("POSTGRES_HOST", "db")
POSTGRES_PORT = env("POSTGRES_PORT", "5432")
POSTGRES_CONN_MAX_AGE = env_int("POSTGRES_CONN_MAX_AGE", 60)

# --- Google OAuth2 -----------------------------------------------------------

GOOGLE_CLIENT_IDS = env_list("GOOGLE_CLIENT_IDS")
GOOGLE_HD_ORG_DOMAIN = env("GOOGLE_HD_ORG_DOMAIN")

# --- Tokens ------------------------------------------------------------------

ACCESS_TOKEN_LIFETIME_MINUTES = env_int("ACCESS_TOKEN_LIFETIME_MINUTES", 60)
REFRESH_TOKEN_LIFETIME_DAYS = env_int("REFRESH_TOKEN_LIFETIME_DAYS", 30)

# --- Throttling --------------------------------------------------------------
# Counts only: settings.py appends the "/min" suffix DRF requires.

THROTTLE_ANON_PER_MIN = env_int("THROTTLE_ANON_PER_MIN", 120)
THROTTLE_SIGNIN_PER_MIN = env_int("THROTTLE_SIGNIN_PER_MIN", 20)
THROTTLE_REFRESH_PER_MIN = env_int("THROTTLE_REFRESH_PER_MIN", 60)
THROTTLE_PUSH_PER_MIN = env_int("THROTTLE_PUSH_PER_MIN", 240)

# --- Security ----------------------------------------------------------------

DJANGO_SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DJANGO_DEBUG)
DJANGO_SECURE_HSTS_SECONDS = env_int(
    "DJANGO_SECURE_HSTS_SECONDS", 0 if DJANGO_DEBUG else 31536000
)
DJANGO_MAX_UPLOAD_BYTES = env_int("DJANGO_MAX_UPLOAD_BYTES", 10 * 1024 * 1024)

# --- Logging -----------------------------------------------------------------

LOG_LEVEL = env("LOG_LEVEL", "INFO").upper()
