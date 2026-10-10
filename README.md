# Gymlarper — backend

Django + Django REST Framework + PostgreSQL API for the Gymlarper workout
tracker. The Flutter client lives separately in `~/projects/gymlarper_ui`.

Offline-first: clients create records with their own UUIDs and reconcile via a
delta-sync protocol, so nothing is ever lost to a dead zone in the gym.

---

## Quick start

```bash
cp .env.example .env          # then edit DJANGO_SECRET_KEY and POSTGRES_PASSWORD
docker compose up --build
```

The API comes up on `http://127.0.0.1:${API_PORT}` (default `8000`), Postgres
on an internal network only.

```bash
make test        # 88 tests, SQLite, no server needed
make smoke       # 43 assertions against a running server over real HTTP
make logs        # tail the API
make shell       # Django shell in the container
make db          # psql inside the Postgres container
```

Requires Docker with the Compose v2 plugin. The container installs strictly
from `Pipfile.lock`, so a `docker compose up --build` needs no Python on the
host.

For local work without Docker:

```bash
pip install pipenv
make install        # pipenv install --deploy
pipenv run python manage.py test --settings=config.test_settings
```

---

## What's in it

| Area | Detail |
|---|---|
| **Auth** | Google ID-token verification, rotating refresh tokens, per-device revocation |
| **Workouts** | Start / stop, exercises, sets (reps, weight, RPE, warm-up / drop / failure) |
| **Machine settings** | Per gym *and* per exercise: name + free-form dials, snapshotted onto the session |
| **Locations** | Home / work / named gyms |
| **Templates** | Nested exercises with prescribed sets and rep ranges; instantiates a full workout |
| **Dashboard** | Volume, streaks, weekly series, per-exercise PRs, muscle split |
| **Sync** | Idempotent push/pull, optimistic concurrency, tombstoned deletes |
| **Units** | kg/lb stored as entered, normalised to kg for all maths |

---

## Dependencies

Managed with [pipenv](https://pipenv.pypa.io).

- `Pipfile` — human-editable constraints (`~=5.2.5` means ">=5.2.5, <5.3").
- `Pipfile.lock` — the exact resolved versions. **Commit it.**

After editing `Pipfile`:

```bash
make lock          # pipenv lock  -> rewrites Pipfile.lock
make install       # reinstall from the lock
```

Docker runs `pipenv install --deploy`, which installs *only* from the lock and
**fails the build if the lock has drifted from the Pipfile**. That is the
guarantee `requirements.txt` could not give you — a stale lock can never be
silently deployed.

Adding a dependency:

```bash
pipenv install "django-filter~=25.1"
```

The lock is generated against Python 3.13, which is what both the Docker image
and `[requires]` target.
apps/
  core/            SyncableModel base, SyncState sequence, error envelope
  accounts/        Google auth, profiles, device sessions
  workouts/        models, serializers, sync engine, stats, views
scripts/           smoke_test.py (end-to-end over real HTTP)
docs/              API.md, SYNC.md  <- read these before writing the client
```

---

## Configuration

All configuration is environment-driven; see `.env.example`. The ones that
matter most:

| Variable | Notes |
|---|---|
| `DJANGO_SECRET_KEY` | required in production |
| `GOOGLE_CLIENT_IDS` | comma-separated Web/Android/iOS client IDs. **Empty ⇒ all sign-ins are rejected.** |
| `DJANGO_ALLOWED_HOSTS` | must list every host the proxy will use |
| `DJANGO_CORS_ALLOWED_ORIGINS` | the Flutter **web** origin(s) |
| `DJANGO_SECURE_SSL_REDIRECT` | auto-`false` when `DJANGO_DEBUG=true` |
| `POSTGRES_PASSWORD` | required; Compose refuses to start without it |

### Google OAuth setup

1. [Cloud console](https://console.cloud.google.com/apis/credentials) → create
   an **OAuth client ID**.
2. Enable the Google Identity API.
3. Put the client ID in `GOOGLE_CLIENT_IDS`.
4. Authorised JS origins: your web origin. Android needs the package name +
   signing fingerprint; iOS needs the bundle ID.

**Client ID note.** `google-auth`'s `verify_oauth2_token` accepts a single
audience. For a Flutter app targeting web *and* mobile, pass the **Web** client
ID as `serverClientId` in `GoogleSignIn` — that is the one ID that is valid in
a browser and on device, so one server setting covers every platform. The server
also accepts a comma-separated list if you prefer separate IDs.

---

## Deploying to your server

```bash
git clone <repo> /opt/gymlarper && cd /opt/gymlarper
cp .env.example .env && $EDITOR .env      # secret key, DB password, domain, Google ID
docker compose -f docker-compose.yml --profile prod up -d --build
```

Using `-f docker-compose.yml` deliberately **skips** `docker-compose.override.yml`,
so you get gunicorn with 3 workers instead of the dev server, and no bind mount
of your source tree.

Then:

```bash
curl -fsS https://your-domain/healthz     # {"status":"ok",...}
docker compose logs -f api
docker compose exec api python manage.py createsuperuser
```

Point `SITE_ADDRESS` at a real domain and Caddy provisions TLS automatically.
`WEB_ROOT` can serve the Flutter web build from the same origin.

**Backups** — the Postgres volume is the only stateful thing:

```bash
docker compose exec -T db pg_dump -U gymlarper gymlarper | gzip > backup-$(date +%F).sql.gz
```

Schedule that in cron. `docker compose down -v` deletes the volume.

---

## Testing

```bash
make test     # unit + API tests on in-memory SQLite
make smoke    # end-to-end over HTTP against the running stack
```

`make smoke` needs a server whose Google verification is stubbed. The repo
ships no auth bypass — the wrapper used for local smoke runs lives outside the
source tree, so there is no code path that skips token verification in
production.

---

## Design notes

**Security posture.** Every endpoint is tenant-scoped: related fields only
resolve to rows the caller owns, and a foreign id is a 400 rather than a 403 so
the API never confirms that someone else's id exists. Sign-in refuses
unverified addresses and refuses to rebind a Google subject that already
belongs to another account. Access tokens carry their device session id and are
checked on every request, so logging out invalidates them immediately instead
of waiting for expiry. The server fails closed with no `DJANGO_SECRET_KEY` and
with no `GOOGLE_CLIENT_IDS`.

**Why a backend at all?** Flutter could verify a Google ID token on-device
against Google's public JWKS. Sessions, revocation, cross-device sync and
Docker hosting all need a trusted store, so the server earns its keep. It
fails *closed*: with `GOOGLE_CLIENT_IDS` unset, every sign-in is rejected
rather than silently trusted.

**Why client-generated UUIDs?** See [`docs/SYNC.md`](docs/SYNC.md). Offline
creation is the whole point.

**Why `rev` on every row?** Optimistic concurrency. The server never silently
clobbers another device's edit; it returns the winning row and lets the client
merge.

**Why snapshot machine settings onto the workout?** "Seat height 3" is true of
*your* gym on *that* day. Editing the setup next month must not rewrite history.

**Why store weights as entered?** Switching a profile from kg to lb never
rewrites history, and a set logged in lb stays readable in lb forever. All
maths normalises through kg.

---

## Known limitations

- No push notifications; sync is pull/push on app lifecycle events.
- No media uploads (progress photos) — `DATA_UPLOAD_MAX_MEMORY_SIZE` is 10 MB.
- Exported PDFs/CSV are not implemented; the raw data is all in `/dashboard/*`.
- Tombstones are never purged, so tables grow monotonically.
- `stats.py` aggregates in Python over `values_list` — fine into the low tens of
  thousands of sets, worth revisiting before a very long history.