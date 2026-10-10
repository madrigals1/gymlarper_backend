# Gymlarper — backend

Django + Django REST Framework + PostgreSQL API for the Gymlarper workout
tracker. The Flutter client lives separately in `~/projects/gymlarper_ui`.

TLS terminates upstream — Cloudflare Tunnel and/or Nginx Proxy Manager. This
stack ships no proxy of its own; the API is only ever reachable through yours.

Offline-first: clients create records with their own UUIDs and reconcile via a
delta-sync protocol, so nothing is ever lost to a dead zone in the gym.

---

## Quick start

```bash
cp .env.example .env          # set DJANGO_SECRET_KEY at minimum
docker compose up -d --build
docker compose logs -f api
docker compose exec api python manage.py shell
```

The API comes up on `http://127.0.0.1:${API_PORT}` (default `8000`) and connects
to the shared Postgres from `~/projects/postgres`. Migrations run on boot.

Requires Docker with the Compose v2 plugin. The container installs strictly
from `Pipfile.lock`, so you need no Python on the host at all.

### Commands worth remembering

Most of this is plain `docker compose`. These five aren't obvious:

```bash
# Tests: SQLite, so no database and no running server needed.
# Or run them on the host with: pipenv run python manage.py test --settings=config.test_settings
docker compose run --rm api python manage.py test --settings=config.test_settings

# Migrations: -u root, or the container user cannot write the file
docker compose run --rm -u root api python manage.py makemigrations

# Production: -f skips docker-compose.override.yml (no bind mount, gunicorn)
docker compose -f docker-compose.yml up -d --build

# psql / pg_dump on the shared server.
# -h 127.0.0.1 is required: its pg_hba.conf rejects local-socket connections.
docker exec -it postgres psql -h 127.0.0.1 -U gymlarper_admin -d gymlarper
docker exec postgres pg_dump -h 127.0.0.1 -U gymlarper_admin gymlarper | gzip > backup.sql.gz
```

There is deliberately no Makefile. It would only wrap `docker compose`.

### Running Python on the host instead

Supported. The lock targets **Python 3.14** — the same major your host has —
and the Docker image uses it too, so both environments match exactly.

```bash
pipenv install --deploy
pipenv run python manage.py test --settings=config.test_settings   # 130 tests
pipenv run python manage.py runserver
```

One catch: `POSTGRES_HOST=postgres` in `.env` is the *container* name on
`database_network`, which does not resolve off the Docker mesh. For host-side
runs override it — the shared Postgres publishes its port to the host:

```bash
POSTGRES_HOST=127.0.0.1 pipenv run python manage.py runserver
```

`pipenv run` reads `.env` automatically, so everything else (secret key, Google
client ID) carries over unchanged.

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

## Database

There is **no database container in this project**. It uses the shared Postgres
from `~/projects/postgres` — the `postgres` container on `database_network`.

```dotenv
POSTGRES_DB=gymlarper
POSTGRES_USER=gymlarper_admin
POSTGRES_HOST=postgres     # container name on the mesh, NOT the published port
POSTGRES_PORT=5432
```

The `gymlarper` database and `gymlarper_admin` role already exist there.
Migrations run automatically on container start.

```bash
docker exec -it postgres psql -h 127.0.0.1 -U gymlarper_admin -d gymlarper
docker exec postgres pg_dump -h 127.0.0.1 -U gymlarper_admin gymlarper | gzip > backup-$(date +%F).sql.gz
```

`-h 127.0.0.1` is required — that server's `pg_hba.conf` has no `local` entries,
so socket connections are refused.

### Least privilege (recommended)

`gymlarper_admin` is a **superuser**, matching the convention your other apps
use — and on `database_network` the API can therefore reach every database on
that server. Verified: those credentials open the `memos` database and read it.
The same holds for xfolio, telespam, l4d2_stats and the rest.

That is fine while this API holds no untrusted input. Once the Flutter client
is live, worth narrowing to what Django actually needs:

```sql
-- applied once, then set POSTGRES_PASSWORD in .env to the new value
ALTER ROLE gymlarper_admin NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT ALL PRIVILEGES ON DATABASE gymlarper TO gymlarper_admin;
```

Django only needs DDL/DML on its own database. Do **not** apply this blindly if
other tooling relies on the superuser role — check first.

## Dependencies

Managed with [pipenv](https://pipenv.pypa.io).

- `Pipfile` — human-editable constraints (`~=6.1.2` means ">=6.1.2, <6.2").
- `Pipfile.lock` — the exact resolved versions. **Commit it.**

After editing `Pipfile`:

```bash
pipenv lock              # rewrites Pipfile.lock
pipenv install --deploy  # reinstall from the lock (also what Docker runs)
```

Docker runs `pipenv install --deploy`, which installs *only* from the lock and
**fails the build if the lock has drifted from the Pipfile**. That is the
guarantee `requirements.txt` could not give you — a stale lock can never be
silently deployed.

Adding a dependency:

```bash
pipenv install "django-filter~=25.1"
```

The lock targets **Python 3.14**, which is what both the Docker base image and
`[requires]` use. Changing either one means re-locking.
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
| `DJANGO_SECURE_SSL_REDIRECT` | auto-`false` when `DJANGO_DEBUG=true`; honours `X-Forwarded-Proto` |
| `API_BIND` / `API_PORT` | where the API is published on the host (default `127.0.0.1:8000`) |
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
docker compose -f docker-compose.yml up -d --build
```

Passing `-f docker-compose.yml` deliberately skips
`docker-compose.override.yml` — you get gunicorn with 3 workers instead of the
dev server, and no bind mount of your source tree.

### Wiring up your tunnel / proxy

The API joins your existing **`https_network`** (the same external network
`cloudflared` and `nginx_proxy_manager` already sit on), driven by the
`HTTPS_NETWORK` variable in `.env` — same convention as your other projects.
The API also joins **`database_network`** to reach the shared Postgres — there
is no private link any more, because the database lives in another project.

Container name: **`gymlarper-api`** (8000).

```
https_network      cloudflared / nginx_proxy_manager  -> gymlarper-api:8000
database_network   gymlarper-api                      -> postgres:5432
```

**Cloudflare Tunnel** — the tunnel is remotely managed, so add the public
hostname in the Zero Trust dashboard:

```
Service:  http://gymlarper-api:8000
```

**Nginx Proxy Manager** — add a Proxy Host:

```
Scheme               HTTP
Forward Hostname     gymlarper-api
Forward Port         8000
```

Both containers resolve that name over `https_network`, so no host port is
involved.

To inspect the database, add a server in pgadmin4 pointing at `postgres:5432`,
database `gymlarper`, user `gymlarper_admin`.

**Settings that matter**

- `DJANGO_ALLOWED_HOSTS` must list your **public** hostname — both proxies
  forward the original `Host` header to Django. Add `gymlarper-api` as well if
  you want to `curl http://gymlarper-api:8000/healthz` from the mesh for
  debugging; Django rejects any Host it does not recognise.
- `DJANGO_SECURE_SSL_REDIRECT=true` is correct here. Django honours
  `X-Forwarded-Proto: https` (set via `SECURE_PROXY_SSL_HEADER`), which both
  proxies send, so only genuine plaintext gets redirected.
- Because that header is trusted, do not expose the API directly to the
  internet — otherwise a client could spoof it. The published port is bound to
  `127.0.0.1` for exactly this reason.
- Add a Cloudflare cache rule to **bypass `/api/*`**. Cloudflare does not cache
  JSON or non-GET by default, but an explicit bypass removes the doubt.
- Anything else on `https_network` (portainer, grafana, memos, …) can reach the
  API directly. That mesh is flat, so treat its members as trusted.
- The API **is** on `database_network`, because that is the only path to the
  shared Postgres. See [Least privilege](#least-privilege-recommended) above:
  `gymlarper_admin` is currently a superuser, so those credentials reach every
  database on that server.

### After deploying

```bash
curl -fsS https://your-domain/healthz     # {"status":"ok",...}
docker compose logs -f api
docker compose exec api python manage.py createsuperuser
```

**Backups** — the data lives in the shared Postgres, so back it up from there
using the `pg_dump` command above. Schedule it in cron alongside the other
projects that share that server.

---

## Testing

```bash
docker compose run --rm api python manage.py test --settings=config.test_settings
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000 --id-token <token>
```

`scripts/smoke_test.py` drives the real HTTP API end to end. It needs a server
whose Google verification is stubbed; the wrapper used for local runs lives
outside the source tree, so there is no code path in this repository that skips
token verification.

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