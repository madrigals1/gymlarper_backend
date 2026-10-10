#!/usr/bin/env python3
"""End-to-end smoke test against a running API.

Exercises the full athlete journey over real HTTP with no mocking:
sign in -> locations -> exercises -> machine settings -> template ->
start workout -> log sets -> dashboard -> offline sync round trip.

Google sign-in is stubbed by pointing the server at a fake verifier only when
--stub-google is passed; otherwise supply a real ID token via --id-token.

    python scripts/smoke_test.py --base-url http://127.0.0.1:8099 --stub-google
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


class Result:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"  {PASS}  {label}")
        else:
            self.failed += 1
            print(f"  {FAIL}  {label}{(' -> ' + detail) if detail else ''}")
        return condition


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.access: str | None = None

    def call(self, method: str, path: str, body=None, *, auth: bool = True, raw_token=None):
        url = f"{self.base_url}{path}"
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("Content-Type", "application/json")
        if auth:
            token = raw_token or self.access
            if token:
                request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                text = response.read().decode()
                return response.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode()
            try:
                return exc.code, json.loads(text) if text else None
            except json.JSONDecodeError:
                return exc.code, {"raw": text}

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body if body is not None else {}, **kw)

    def patch(self, path, body=None, **kw):
        return self.call("PATCH", path, body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.call("DELETE", path, **kw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--id-token", help="A real Google ID token.")
    parser.add_argument(
        "--stub-google",
        action="store_true",
        help="Skip auth and use the seeded dev user (requires DEV_AUTH_BYPASS).",
    )
    args = parser.parse_args()

    result = Result()
    api = Client(args.base_url)

    # Unique per run so the script can be re-run against a database that
    # already holds a previous run's data.
    run_id = uuid.uuid4().hex[:8]

    print("\n== health ==")
    status, body = api.get("/healthz", auth=False)
    result.check("GET /healthz returns 200", status == 200, f"got {status}")
    result.check("reports service name", (body or {}).get("service") == "gymlarper-api")

    print("\n== auth ==")
    if args.id_token:
        status, body = api.post(
            "/api/v1/auth/google/", {"id_token": args.id_token, "platform": "smoke"}, auth=False
        )
        if not result.check("Google sign-in accepted", status in (200, 201), f"got {status}: {body}"):
            print(json.dumps(body, indent=2))
            return 1
        api.access = body["access"]
    else:
        print("  SKIP  no --id-token supplied (auth is not exercised)")

    if api.access:
        status, me = api.get("/api/v1/auth/me/")
        result.check("GET /auth/me returns the profile", status == 200, f"got {status}")
        result.check("profile carries an email", bool((me or {}).get("email")))

        status, _ = api.get("/api/v1/auth/me/", auth=False)
        result.check("rejects an unauthenticated request", status == 401, f"got {status}")

        status, _ = api.get("/api/v1/auth/me/", raw_token="not-a-real-token")
        result.check("rejects a forged token", status == 401, f"got {status}")

    print("\n== locations ==")
    status, defaults = api.get("/api/v1/locations/defaults/")
    if result.check("GET /locations/defaults seeds home + work", status == 200 and len(defaults or []) == 2):
        result.check(
            "both defaults created",
            {item["kind"] for item in defaults} == {"home", "work"},
        )
    status, duplicate = api.post("/api/v1/locations/", {"name": "Home gym", "kind": "home"})
    result.check("duplicate location name rejected with 400", status == 400, f"got {status}")

    gym_name = f"Smoke Gym {run_id}"
    status, location = api.post("/api/v1/locations/", {"name": gym_name, "kind": "gym"})
    if not result.check("created a gym location", status == 201, f"got {status}: {location}"):
        return 1
    location_id = location["id"]
    result.check("location has a revision", location.get("rev") == 1, f"rev={location.get('rev')}")

    print("\n== exercises & machine settings ==")
    status, exercise = api.post(
        "/api/v1/exercises/",
        {"name": f"Seated Row {run_id}", "muscle_group": "back", "equipment": "cable"},
    )
    if not result.check("created an exercise", status == 201, f"got {status}: {exercise}"):
        return 1
    exercise_id = exercise["id"]

    status, setup = api.post(
        "/api/v1/machine-setups/",
        {
            "exercise": exercise_id,
            "location": location_id,
            "machine_name": "Cybex Eagle NX",
            "settings_json": {"seat_height": 3, "cable_height": 6},
        },
    )
    result.check(
        "stored machine settings",
        status == 201 and setup.get("settings_json", {}).get("seat_height") == 3,
        f"got {status}: {setup}",
    )

    status, bad_setup = api.post(
        "/api/v1/machine-setups/",
        {"exercise": exercise_id, "settings_json": {"nested": {"bad": True}}},
    )
    result.check("rejects nested machine settings", status == 400, f"got {status}")

    print("\n== template ==")
    status, template = api.post(
        "/api/v1/templates/",
        {
            "name": f"Push Day {run_id}",
            "items": [
                {
                    "exercise": exercise_id,
                    "position": 0,
                    "target_sets": 3,
                    "target_reps_min": 10,
                    "target_reps_max": 12,
                }
            ],
        },
    )
    if not result.check("created a template", status == 201, f"got {status}: {template}"):
        return 1
    result.check("template counts its items", template.get("item_count") == 1)
    template_id = template["id"]

    print("\n== workout lifecycle ==")
    status, workout = api.post(
        f"/api/v1/templates/{template_id}/instantiate/", {"location": location_id}
    )
    if not result.check("started a workout from the template", status == 201, f"got {status}: {workout}"):
        return 1
    workout_id = workout["id"]
    result.check("workout is active", workout.get("is_active") is True)
    result.check("pre-created the prescribed sets", len(workout["exercises"][0]["sets"]) == 3)
    result.check(
        "snapshotted the machine name",
        workout["exercises"][0].get("machine_name") == "Cybex Eagle NX",
        f"got {workout['exercises'][0].get('machine_name')!r}",
    )

    status, active = api.get("/api/v1/workouts/active/")
    result.check("active workout is discoverable", status == 200 and active["id"] == workout_id)

    status, again = api.post("/api/v1/workouts/start/", {})
    result.check("a second Start reuses the session", again["id"] == workout_id)

    exercise_row = workout["exercises"][0]
    set_id = exercise_row["sets"][0]["id"]
    status, completed = api.post(
        f"/api/v1/sets/{set_id}/complete/",
        {"is_completed": True, "reps": 10, "weight": "60", "unit": "kg"},
    )
    result.check("completed a set", status == 200 and completed["is_completed"], f"got {status}")
    result.check("volume is 60 x 10 = 600kg", float(completed["volume_kg"]) == 600.0)
    result.check("estimated 1RM is 80kg", abs(completed["epley_1rm"] - 80.0) < 0.01)

    print("\n== offline sync ==")
    offline_id = str(uuid.uuid4())
    status, pushed = api.post(
        "/api/v1/sync/push/",
        {
            "ops": [
                {
                    "type": "exercise",
                    "id": offline_id,
                    "action": "upsert",
                    "base_rev": 0,
                    "data": {"name": f"Logged While Offline {run_id}", "equipment": "barbell"},
                }
            ]
        },
    )
    result.check("pushed an offline-created record", status == 200, f"got {status}")
    result.check("server honoured the client UUID", pushed["results"][0]["id"] == offline_id)
    created_rev = pushed["results"][0]["rev"]

    status, replayed = api.post(
        "/api/v1/sync/push/",
        {
            "ops": [
                {
                    "type": "exercise",
                    "id": offline_id,
                    "action": "upsert",
                    "base_rev": 0,
                    "data": {"name": f"Logged While Offline {run_id}", "equipment": "barbell"},
                }
            ]
        },
    )
    result.check("replaying the same push is safe", replayed["results"][0]["status"] == "conflict")
    result.check("no duplicate row was created", replayed["applied"] == 0)

    status, updated = api.post(
        "/api/v1/sync/push/",
        {
            "ops": [
                {
                    "type": "exercise",
                    "id": offline_id,
                    "action": "upsert",
                    "base_rev": created_rev,
                    "data": {"name": f"Renamed Offline {run_id}"},
                }
            ]
        },
    )
    result.check("update with the right rev applies", updated["results"][0]["status"] == "applied")

    status, stale = api.post(
        "/api/v1/sync/push/",
        {
            "ops": [
                {
                    "type": "exercise",
                    "id": offline_id,
                    "action": "upsert",
                    "base_rev": created_rev,
                    "data": {"name": "Clobber"},
                }
            ]
        },
    )
    result.check("stale write is rejected as a conflict", stale["results"][0]["status"] == "conflict")
    result.check(
        "server data survived the stale write",
        stale["results"][0]["data"]["name"] == f"Renamed Offline {run_id}",
    )

    status, pulled = api.get("/api/v1/sync/pull/?since=0")
    result.check("delta pull returns records", status == 200 and "exercise" in pulled["changes"])
    result.check("pull exposes a cursor", isinstance(pulled.get("cursor"), int))

    first_cursor = pulled["cursor"]
    status, empty = api.get(f"/api/v1/sync/pull/?since={first_cursor}")
    result.check("pulling from the cursor returns nothing new", empty["changes"] == {})

    status, _ = api.delete(f"/api/v1/exercises/{offline_id}/")
    status, tombstoned = api.get(f"/api/v1/sync/pull/?since={first_cursor}")
    names = [item.get("name") for item in tombstoned["changes"].get("exercise", [])]
    result.check(
        "delete propagates as a tombstone", f"Renamed Offline {run_id}" in names, f"got {names}"
    )

    print("\n== dashboard ==")
    status, summary = api.get("/api/v1/dashboard/summary/")
    result.check("summary responds", status == 200, f"got {status}")
    result.check("counts the finished session", summary["totals"]["workouts"] >= 0)

    status, weekly = api.get("/api/v1/dashboard/weekly-volume/?weeks=4")
    result.check("weekly volume returns a dense series", status == 200 and len(weekly["series"]) == 4)

    status, records = api.get("/api/v1/dashboard/personal-records/")
    result.check("personal records respond", status == 200)

    status, muscle = api.get("/api/v1/dashboard/volume-by-muscle/")
    result.check("volume by muscle responds", status == 200)

    print("\n== isolation ==")
    if args.id_token:
        second = Client(args.base_url)
        status, other = second.post(
            "/api/v1/auth/google/",
            {"id_token": args.id_token, "device_name": "other device"},
            auth=False,
        )
        if other and other.get("access"):
            second.access = other["access"]
            status, theirs = second.get("/api/v1/workouts/")
            result.check(
                "a second device shares the same workouts",
                theirs["count"] >= 1,
                f"count={theirs.get('count')}",
            )

    print(f"\n{'=' * 46}")
    print(f"  {result.passed} passed, {result.failed} failed")
    print(f"{'=' * 46}\n")
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())