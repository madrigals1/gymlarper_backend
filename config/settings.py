"""Django settings for the Gymlarper backend.

Configuration is entirely environment-driven so the same image can run in dev
and production. Every value comes from `config/env.py`; this module only maps
them onto the names Django expects.
"""

from __future__ import annotations

from datetime import timedelta

from config import env

# --- Core --------------------------------------------------------------------

DEBUG = env.DJANGO_DEBUG
SECRET_KEY = env.DJANGO_SECRET_KEY
ALLOWED_HOSTS = env.DJANGO_ALLOWED_HOSTS
BASE_DIR = env.BASE_DIR

PUBLIC_ORIGIN = env.DJANGO_PUBLIC_ORIGIN

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "django_filters",
    "apps.core",
    "apps.accounts",
    "apps.workouts",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ----------------------------------------------------------------

if env.DJANGO_USE_SQLITE:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env.POSTGRES_DB,
            "USER": env.POSTGRES_USER,
            "PASSWORD": env.POSTGRES_PASSWORD,
            "HOST": env.POSTGRES_HOST,
            "PORT": env.POSTGRES_PORT,
            "CONN_MAX_AGE": env.POSTGRES_CONN_MAX_AGE,
            "OPTIONS": {"connect_timeout": 10},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n / static -----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = env.DJANGO_TIME_ZONE
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- DRF ---------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.accounts.authentication.SessionBoundJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.CursorPageNumberPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    # M2: unauthenticated endpoints are the ones worth rate limiting.
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ),
    # DRF parses these as "<count>/<period>"; the period suffix is required.
    "DEFAULT_THROTTLE_RATES": {
        "anon": f"{env.THROTTLE_ANON_PER_MIN}/min",
        "google_signin": f"{env.THROTTLE_SIGNIN_PER_MIN}/min",
        "token_refresh": f"{env.THROTTLE_REFRESH_PER_MIN}/min",
        "sync_push": f"{env.THROTTLE_PUSH_PER_MIN}/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.ACCESS_TOKEN_LIFETIME_MINUTES),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.REFRESH_TOKEN_LIFETIME_DAYS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# --- CORS --------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env.DJANGO_CORS_ALLOWED_ORIGINS
CORS_ALLOW_CREDENTIALS = True
# Admin/login forms are cookie-authenticated, so they need CSRF trust too.
CSRF_TRUSTED_ORIGINS = env.DJANGO_CSRF_TRUSTED_ORIGINS or CORS_ALLOWED_ORIGINS

# --- Google OAuth2 -----------------------------------------------------------

GOOGLE_CLIENT_IDS = env.GOOGLE_CLIENT_IDS
GOOGLE_HD_ORG_DOMAIN = env.GOOGLE_HD_ORG_DOMAIN

# --- Security ----------------------------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.DJANGO_SECURE_SSL_REDIRECT
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = env.DJANGO_SECURE_HSTS_SECONDS
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
X_FRAME_OPTIONS = "DENY"

DATA_UPLOAD_MAX_MEMORY_SIZE = env.DJANGO_MAX_UPLOAD_BYTES

# --- Logging -----------------------------------------------------------------

LOG_LEVEL = env.LOG_LEVEL
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)-8s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}
