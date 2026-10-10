#!/usr/bin/env sh
# Container entrypoint: wait for Postgres, migrate, then hand over to gunicorn.
set -e

echo "==> waiting for database"
if [ "${1#gunicorn}" != "$1" ] || [ "${1#uvicorn}" != "$1" ]; then
python - <<'PY'
import os, sys, time
from urllib.parse import urlparse

if os.environ.get("DJANGO_USE_SQLITE", "").lower() in {"1", "true", "yes"}:
    print("    sqlite in use, skipping wait")
    sys.exit(0)

import socket

host = os.environ.get("POSTGRES_HOST", "db")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
deadline = time.time() + int(os.environ.get("DB_WAIT_SECONDS", "60"))

while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"    {host}:{port} is up")
            sys.exit(0)
    except OSError:
        time.sleep(1)

print(f"    timed out waiting for {host}:{port}", file=sys.stderr)
sys.exit(1)
PY
else
  echo "    not a server process, skipping wait"
fi

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "==> applying migrations"
  python manage.py migrate --noinput
fi

if [ "${COLLECT_STATIC:-true}" = "true" ]; then
  echo "==> collecting static files"
  python manage.py collectstatic --noinput --clear >/dev/null
fi

if [ "${CREATE_SUPERUSER:-false}" = "true" ]; then
  echo "==> ensuring superuser exists"
  python manage.py createsuperuser --noinput || true
fi

echo "==> starting: $*"
exec "$@"