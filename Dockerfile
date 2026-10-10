# syntax=docker/dockerfile:1

# ---- Base ------------------------------------------------------------------
# Pinned to a patch release so rebuilds are reproducible.
FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# libpq is needed at runtime by psycopg; build tooling only in the builder.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---- Dependencies ----------------------------------------------------------
# Copied on their own so the (slow) install layer is reused whenever app code
# changes.
FROM base AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# pipenv lives in the builder's system Python only. Installing it into the venv
# would ship it (and virtualenv, filelock, distlib...) into the final image.
RUN pip install --no-cache-dir --root-user-action=ignore pipenv

# The virtualenv is created at its *final* path. venv console scripts hardcode
# an absolute shebang, so a venv built at /srv/.venv and copied to /opt/venv
# would leave every entrypoint pointing at a path that no longer exists.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv \
    PIPENV_NOSPIN=1 \
    PIPENV_VERBOSITY=-1

COPY Pipfile Pipfile.lock ./

# --deploy installs strictly from Pipfile.lock and fails the build if the lock
# has drifted from the Pipfile. With VIRTUAL_ENV set, pipenv targets that venv.
RUN pipenv install --deploy \
    && /opt/venv/bin/pip check

# ---- Runtime ----------------------------------------------------------------
FROM base AS runtime

RUN groupadd --system app && useradd --system --gid app --create-home app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv

COPY --chown=app:app . /app
# WORKDIR is created as root, so give the unprivileged user ownership of the
# tree plus the writable directories the entrypoint needs.
RUN mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app \
    && chmod +x /app/entrypoint.sh

# Drop privileges: the API never needs root.
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]