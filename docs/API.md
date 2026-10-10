# API reference

Base URL: `https://your-domain/api/v1`
Everything except `/auth/google/`, `/auth/refresh/` and `/healthz` requires
`Authorization: Bearer <access_token>`.

Responses are JSON. Timestamps are ISO-8601 UTC.

---

## Error envelope

Every non-2xx response has the same shape, so the client can render one thing:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The submitted data was invalid.",
    "details": { "name": ["You already have a gymlocation called 'Home gym'."] }
  }
}
```

| Status | `code`                     | Meaning                                        |
|--------|----------------------------|------------------------------------------------|
| 400    | `validation_error`         | Bad input; `details` maps field -> messages    |
| 401    | `invalid_token`            | Google ID token rejected                       |
| 401    | `invalid_refresh`          | Refresh token malformed or expired             |
| 401    | `session_revoked`          | Signed out, or token already rotated           |
| 401    | `wrong_domain`             | Email domain not allowed (if `GOOGLE_HD_ORG_DOMAIN` set) |
| 401    | `unverified_email`         | Google reports the address as unverified       |
| 401    | `not_configured`           | Server has no `GOOGLE_CLIENT_IDS` (misconfigured deploy) |
| 403    | `identity_conflict`        | Google identity does not match the account     |
| 404    | —                          | Not found, or owned by someone else            |
| 409    | `identity_conflict`        | That Google subject is already linked elsewhere |
| 413    | `batch_too_large`          | More than 500 ops in one push                  |
| 429    | `throttled`                | Rate limit hit (see below)                     |

**Referencing another athlete's id is a 400, not a 403.** If you send a
`workout`, `exercise`, `location` or `template` id that is not yours, the
related field fails validation. This is deliberate: the API does not confirm
whether an id exists elsewhere.

**A 404 is also returned for records belonging to another user.** There is no
way to distinguish "does not exist" from "not yours", by design.

---

## Pagination

List endpoints return:

```json
{ "count": 42, "num_pages": 1, "page": 1, "page_size": 50,
  "next": null, "previous": null, "results": [ ... ] }
```

`?page_size=` (max 200) and `?page=` are supported.

---

## Auth

### `POST /auth/google/`

Exchange a Google ID token for an API session.

```json
{ "id_token": "<from google_sign_in>",
  "device_name": "Pixel 9", "platform": "android", "app_version": "1.0.0" }
```

`201` (new user) or `200` (returning user):

```json
{ "access": "eyJ...", "refresh": "eyJ...", "session_id": 3,
  "expires_in": 3600, "is_new_user": true,
  "user": { "id": 1, "email": "ada@example.com", "display_name": "Ada Lovelace",
            "google_picture": "", "units": "kg", "default_location": null,
            "bodyweight_kg": null, "created_at": "...", "updated_at": "..." } }
```

The last three `device_*` fields are optional but power the "your devices"
screen.

Two sign-in refusals worth handling explicitly:

- **`unverified_email`** — Google says the address is not verified. The server
  will not create or match an account on it.
- **`409 identity_conflict`** — that Google subject is already linked to a
  different account. Show the user a "this account is linked elsewhere" screen;
  do not retry.

### Rate limits

Per IP, per minute, configurable via `THROTTLE_*` env vars:

| Scope | Endpoint | Default |
|---|---|---|
| `anon` | any unauthenticated request | 120 |
| `google_signin` | `POST /auth/google/` | 20 |
| `token_refresh` | `POST /auth/refresh/` | 60 |
| `sync_push` | `POST /sync/push/` | 240 |

A 429 carries `Retry-After`. Back off exponentially; do not hammer.

### `POST /auth/refresh/`

```json
{ "refresh": "eyJ..." }
```

Returns a **new** access *and* refresh token. The old refresh token is
invalidated immediately — persist the new one atomically, or a crash mid-write
logs the athlete out.

### `POST /auth/logout/`
Revokes **every** session for the user. `204`. Access tokens issued to those
sessions stop working on the very next request — tokens carry their session id
and every authenticated call checks it, so revocation is immediate rather than
"when the token expires".

### `POST /auth/password/`
Set a password for an account that cannot (or does not want to) use Google.

Accounts created through Google have an *unusable* password, so there is no
current password to check. Prove control instead:

```json
{ "google_id_token": "<fresh token>", "new_password": "a-strong-password" }
```

The token's subject must already be linked to the account, otherwise `403`.
If the account *does* have a usable password, send `current_password` instead.

### `GET /auth/me/` · `PATCH /auth/me/`
`PATCH` accepts `units` (`"kg"`/`"lb"`), `default_location`, `bodyweight_kg`.

### `GET /auth/devices/` · `POST /auth/devices/{id}/revoke/`
Revoking a device also invalidates its live access tokens immediately.

---

## Locations

`GET|POST /locations/` · `GET|PATCH|DELETE /locations/{id}/`

```json
{ "id": "uuid", "rev": 1, "name": "Home gym",
  "kind": "home|work|gym|other",
  "address": "", "notes": "", "is_archived": false, "color": "#22c55e",
  "created_at": "...", "updated_at": "...", "deleted_at": null }
```

`DELETE` is a soft delete (tombstone) so the removal syncs to other devices.

### `GET /locations/defaults/`
Idempotently creates and returns the "Home gym" + "Work gym" defaults. Call once
after first sign-in. Returns both rows every time, so it is safe to repeat.

---

## Exercises

`GET|POST /exercises/` · `GET|PATCH|DELETE /exercises/{id}/`

```json
{ "id": "uuid", "rev": 1, "name": "Seated Cable Row",
  "muscle_group": "back", "equipment": "cable",
  "notes": "", "is_machine": true, "metrics": {}, "archived": false,
  "created_at": "...", "updated_at": "...", "deleted_at": null }
```

- `muscle_group`: `chest back shoulders biceps triceps forearms quads hamstrings glutes calves core full_body other`
- `equipment`: `barbell dumbbell machine cable smith kettlebell band bodyweight cardio other`
- `metrics`: free-form counters for cardio, e.g. `{"distance_m": 5000, "time_s": 1500}`

Filters: `?muscle_group=back&equipment=cable&archived=false`.
Archived rows are hidden unless `?include_archived=true`.

### `POST /exercises/seed/`
Bulk-creates a starter catalogue. Idempotent. Returns `{"created": n, "skipped": n}`.

---

## Machine setups

This is the "height 3 on the cable machine, height 6 on the seated row" feature.

`GET|POST /machine-setups/` · `GET|PATCH|DELETE /machine-setups/{id}/`

```json
{ "id": "uuid", "rev": 1,
  "exercise": "uuid", "exercise_name": "Seated Cable Row",
  "location": "uuid",  "location_name": "Work gym",
  "machine_name": "Cybex Eagle NX",
  "settings_json": { "seat_height": 3, "cable_height": 6 },
  "notes": "", "created_at": "...", "updated_at": "...", "deleted_at": null }
```

`settings_json` is a **flat** object of `string -> number | string | null`.
Nesting is rejected with a 400. `location` may be `null` for a setup that
applies anywhere.

### `GET /machine-setups/defaults/?exercise={id}&location={id}`
Returns the remembered setup to pre-fill a form: exact location match first,
most-recently-used as fallback, `{"machine_name": "", "settings_json": {}}` if
there is none.

---

## Templates

`GET|POST /templates/` · `GET|PATCH|DELETE /templates/{id}/`

```json
{ "id": "uuid", "rev": 1, "name": "Legs day",
  "description": "", "notes": "", "default_location": null, "default_unit": "kg",
  "archived": false, "items_version": 1, "item_count": 2,
  "items": [ { "id": "uuid", "rev": 1, "template": "uuid",
               "exercise": "uuid", "exercise_name": "Leg Press",
               "position": 0, "target_sets": 3,
               "target_reps_min": 8, "target_reps_max": 12,
               "target_weight": "120.00", "target_rpe": "8.0",
               "rest_seconds": 120, "notes": "" } ],
  "created_at": "...", "updated_at": "...", "deleted_at": null }
```

`items` is **write-only**: send it on `POST`/`PATCH` to replace the routine.
Omit it on `PATCH` to leave items untouched. Sending it replaces all items and
bumps `items_version`.

`target_reps_min` must be `<= target_reps_max`.

### `POST /templates/{id}/instantiate/`
Starts a workout pre-filled from the template. Body is optional:

```json
{ "location": "uuid", "name": "Legs day", "started_at": "2026-10-10T18:00:00Z" }
```

Creates the workout, one `workout_exercise` per item with **snapshotted**
machine name + settings, and `target_sets` empty working sets. Returns the full
workout (`201`).

### `POST /templates/{id}/items/reorder/`
```json
{ "order": ["<item-uuid>", "<item-uuid>"] }
```

---

## Workouts

`GET /workouts/` · `GET|PATCH|DELETE /workouts/{id}/`

```json
{ "id": "uuid", "rev": 1,
  "template": "uuid", "template_name": "Legs day",
  "location": "uuid", "location_name": "Home gym",
  "name": "", "notes": "", "started_at": "...", "finished_at": null,
  "unit": "kg", "bodyweight": null, "bodyweight_unit": "kg",
  "display_name": "Legs day", "is_active": true,
  "duration_seconds": null, "volume": 0.0,
  "exercises": [ ... ],
  "created_at": "...", "updated_at": "...", "deleted_at": null }
```

Filters: `?status=active|finished&location={id}&template={id}&since={iso8601}`

### `GET /workouts/active/`
The in-progress session, or `404`. Call on app launch to restore state.

### `POST /workouts/start/`
```json
{ "location": "uuid", "name": "", "unit": "kg", "started_at": "iso8601",
  "template": "uuid" }
```

- With `template`, delegates to `templates/{id}/instantiate/`.
- Without, creates an empty session.
- If a session is already running, returns **that one** with `200` instead of
  creating a second. Tapping Start twice is safe.

### `POST /workouts/{id}/finish/`
```json
{ "finished_at": "iso8601", "notes": "", "bodyweight": "78.4", "bodyweight_unit": "kg" }
```
Idempotent: finishing twice keeps the first `finished_at`.

### `POST /workouts/{id}/exercises/`
Adds an exercise, pre-filling machine name/settings from the most recent setup
for that exercise at that location (falling back to any location). Pre-creates
`target_sets` rows.

```json
{ "exercise": "uuid", "position": 0, "target_sets": 3,
  "machine_name": "", "machine_settings": {} }
```

**Snapshots:** the machine name and settings are copied onto
`workout_exercise` at this moment. Editing the setup later never rewrites what
the athlete actually used.

### `POST /workouts/{id}/exercises/reorder/`
`{"order": ["uuid", ...]}`

---

## Workout exercises & sets

`GET /workout-exercises/{id}` · `PATCH` · `DELETE`
`POST /workout-exercises/{id}/add_set/` — appends a set copying weight/reps
from the previous one. Returns the new set.

```json
{ "id": "uuid", "rev": 1, "workout": "uuid", "exercise": "uuid",
  "exercise_name": "Leg Press", "display_name": "Leg Press",
  "muscle_group": "quads", "equipment": "machine",
  "machine_name": "Hack Squat 45", "machine_settings": { "seat_height": 3 },
  "position": 0, "target_sets": 3, "target_reps_min": 8, "target_reps_max": 12,
  "notes": "", "completed_sets": 2,
  "sets": [ ... ],
  "created_at": "...", "updated_at": "...", "deleted_at": null }
```

### A set

```json
{ "id": "uuid", "rev": 1, "workout_exercise": "uuid",
  "position": 0, "reps": 10, "weight": "100.00", "unit": "kg",
  "set_type": "working", "is_bodyweight": false, "rpe": null,
  "is_completed": true, "completed_at": "...", "notes": "",
  "volume_kg": 1000.0, "epley_1rm": 133.33,
  "created_at": "...", "updated_at": "...", "deleted_at": null }
```

- `set_type`: `working | warmup | drop | failure`
- `volume_kg` — reps x weight normalised to kg. `0` for warm-ups, incomplete
  sets, and bodyweight sets with no load. Server-computed.
- `epley_1rm` — `weight * (1 + reps/30)`, kg. Server-computed. `0` for
  warm-ups/incomplete sets.

### `POST /sets/{id}/complete/`
The highest-frequency call in the app — designed to be one tap.

```json
{ "is_completed": true, "reps": 10, "weight": "100", "rpe": 8 }
```

Only `is_completed` is required; the rest override if present. Unchecking
clears `completed_at` and zeroes the derived volume.

---

## Dashboard

Useful when the client has no local data (e.g. first sync after reinstall, or a
quick web check). For normal use, compute locally from the synced data so the
dashboard works offline.

| Endpoint | Returns |
|---|---|
| `GET /dashboard/summary/` | totals, this week, streaks, 5 recent PRs |
| `GET /dashboard/weekly-volume/?weeks=12` | dense weekly series |
| `GET /dashboard/personal-records/?limit=50` | best est. 1RM per exercise |
| `GET /dashboard/volume-by-muscle/?days=90` | volume split by muscle |
| `GET /dashboard/exercise-progress/{exercise_id}/` | every set for one exercise |
| `GET /dashboard/machines/` | machines you actually train on |

All accept `?unit=kg|lb` (defaults to the profile's unit).

```json
// GET /dashboard/summary/
{ "generated_at": "...", "lookback_days": 365,
  "totals": { "workouts": 42, "sets": 380, "volume_kg": 184220.5,
              "training_minutes": 6240 },
  "this_week": { "workouts": 3, "volume_kg": 18400.0 },
  "streaks": { "current_day_streak": 4, "longest_day_streak": 17,
               "current_week_streak": 3, "last_workout_at": "..." },
  "recent_prs": [ ... ] }
```

```json
// GET /dashboard/weekly-volume/?weeks=4&unit=lb
{ "unit": "lb", "series": [ { "week_start": "2026-09-21T00:00:00Z",
                              "volume": 0.0, "volume_kg": 0.0,
                              "workouts": 0 }, ... ] }
```

The series is **dense** — one entry per week including empty ones, so a chart
never has to infer missing bars. `volume_kg` is always the truth; `volume` is
the same number rendered in the requested unit.

---

## Sync

See [`SYNC.md`](./SYNC.md) for the full protocol and a Dart implementation
sketch. Short version:

- `POST /sync/push/` — send local edits
- `GET /sync/pull/?since={cursor}` — fetch everything changed since a cursor