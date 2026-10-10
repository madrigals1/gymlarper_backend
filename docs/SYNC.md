# Sync protocol

The client is offline-first. Every write lands on the device immediately and is
reconciled with the server when a connection is available. Nothing in the UI
should ever wait on the network — a gym basement has no signal and the athlete
still needs to log their sets.

## Why client-generated ids

Every syncable record has a **UUID primary key minted by the client**. A phone
can create a workout at the bottom of a dead-zone gym, get in a car, drive home
on mobile data, and push a record whose id the server has never seen. There is
no "create this on the server and tell me what to call it" round trip.

`user` is always assigned server-side from the access token. A `user` key inside
`data` is stripped.

## Optimistic concurrency: `rev`

Each row carries a `rev` that increments on every server-side write.

- Create a row → `rev == 1`.
- Push an update → send `base_rev` = the `rev` you last saw.
- Server compares. Equal → apply and bump. Different → **conflict**.

A mismatch means another device got there first. The server does **not**
overwrite. It returns its winning row so the client can merge:

```json
{ "id": "...", "type": "exercise", "status": "conflict",
  "rev": 7, "server_rev": 7,
  "data": { "id": "...", "rev": 7, "name": "Renamed on the other phone", ... } }
```

Suggested client merge: compare your local `updatedAt` against the returned
`updated_at`. Newer local edit wins (re-push with `base_rev = server_rev`);
otherwise adopt `data`.

In practice, single-device or sequential use never conflicts. The protocol
exists so a genuine race degrades to "pick one" rather than "lose data".

## Deletion

Deletes are **tombstones**, not row removal: `deleted_at` is set and the row
stays. Otherwise deleting a set on your phone would make it reappear on sync,
because the server would still have the old row and the client would re-push it.

`action: "delete"` is idempotent — deleting an already-deleted row succeeds.

## Sequence numbers and the pull cursor

Each user has one monotonically increasing counter. Every write takes the next
number and stamps it on the row.

```sql
UPDATE core_syncstate SET seq = seq + 1 WHERE user_id = ? RETURNING seq;
```

So `GET /sync/pull/?since=N` is a single indexed query per type:

```sql
SELECT * FROM workouts_exercise
WHERE user_id = ? AND seq > N AND deleted_at IS NULL
ORDER BY seq;
```

No offset pagination, so nothing can be skipped by a row changing mid-scan.
`cursor` in the response is the highest `seq` returned; if `has_more` is true,
pull again from it.

## Pull

```
GET /api/v1/sync/pull/?since={cursor}&limit={perType}
```

```json
{ "cursor": 812,
  "has_more": false,
  "changes": {
    "location": [ { "id": "...", "rev": 1, "name": "Home gym", ... } ],
    "exercise": [ ... ],
    "workout":   [ ... ]
  },
  "server_time": "2026-10-10T08:12:00Z",
  "types": ["exercise", "location", "machine_setup", "template",
            "template_item", "workout", "workout_exercise", "workout_set"] }
```

Apply each row by primary key, honouring `deleted_at` as a local delete.
Advance the stored cursor to `cursor` **only after** the rows are written.

**On `has_more`, keep pulling.** When a type is truncated the server returns a
cursor set to the lowest watermark *any* type reached, not the global maximum.
That guarantees no row is skipped; it just means rows already delivered may be
sent twice, which is harmless because application is idempotent by primary key.

| Type | Parent |
|---|---|
| `location` | — |
| `exercise` | — |
| `machine_setup` | `exercise`, optional `location` |
| `template` | — |
| `template_item` | `template`, `exercise` |
| `workout` | optional `template`, optional `location` |
| `workout_exercise` | `workout`, optional `exercise` |
| `workout_set` | `workout_exercise` |

## Push

```
POST /api/v1/sync/push/
{ "ops": [ { "type": "exercise", "id": "<client uuid>", "action": "upsert",
             "base_rev": 0, "data": { ... } } ] }
```

Max **500 ops** per request (`413` beyond that). Split larger backlogs.

Results come back per op, in order:

```json
{ "results": [ { "id": "...", "type": "exercise", "status": "applied",
                 "rev": 2, "data": { ... } } ],
  "applied": 1, "conflicts": 0,
  "server_time": "..." }
```

`status` is one of:

| Status | Meaning | Client action |
|---|---|---|
| `applied` | Accepted | clear the dirty flag, store `rev` |
| `conflict` | Another device won | merge, then re-push if yours was newer |
| `rejected` | Invalid data or bad reference | **surface the error**; do not retry forever |

A rejected op looks like:

```json
{ "id": "...", "type": "workout_set", "status": "rejected",
  "error": "workout_set: {'workout_exercise': ['Referenced record does not exist.']}" }
```

The message is safe to show a user: internal exception text is logged
server-side and never returned. One bad op does not poison the batch — each
runs in its own savepoint, so partial success is normal and expected.

`POST /sync/push/` is rate limited (240/min by default). Back off on `429`.

### Ordering

Ops are applied in array order. Send parents before children: a `workout`
before its `workout_exercise`, that before its `workout_set`.

### Idempotency

Replaying a push is safe. A create replayed with `base_rev: 0` against a row now
at `rev 1` returns `conflict` rather than creating a duplicate — so a retried
POST after a dropped response will not double-write. Compare `status` against
what you already know locally and treat `conflict` on an identical replay as
success.

## Suggested sync loop

```dart
class SyncEngine {
  // Push dirty local rows, then pull server changes, repeatedly until quiet.
  Future<SyncReport> sync() async {
    var report = SyncReport();
    for (var round = 0; round < 5; round++) {
      final pushed = await pushDirtyOps();
      report.applied += pushed.applied;
      report.conflicts += pushed.conflicts;
      report.rejected += pushed.rejected;

      final pulled = await pullSince(cursor);
      await applyToLocal(pulled.changes);
      cursor = pulled.cursor;

      if (!pushed.hadWork) break;      // nothing dirty, done
      if (!pulled.hasMore) break;       // caught up, done
    }
    return report;
  }
}
```

Trigger it on: app launch, app resume, after a mutation (debounced ~2s), on
connectivity regained. Never block a UI action on it.

## Local schema

Mirror the server tables. Extra columns the client owns:

```sql
-- on every table
is_dirty   INTEGER NOT NULL DEFAULT 0,  -- has unsynced local edits
base_rev   INTEGER NOT NULL DEFAULT 0,  -- rev we last saw from the server
local_updated_at INTEGER                 -- device clock, for merge decisions
```

Suggested: a partial index `WHERE is_dirty = 1` to make draining the outbox
cheap, plus an outbox table recording the exact op JSON so a crash mid-push
replays identically.

## Drift mapping

```dart
class GymLocations extends Table {
  TextColumn get id => text()();               // client-generated UUID
  IntColumn  get rev => integer().withDefault(const Constant(0))();
  TextColumn get name => text().withLength(min: 1, max: 120)();
  TextColumn get kind => text()();
  TextColumn get address => text().withDefault(const Constant(''))();
  TextColumn get notes => text().withDefault(const Constant(''))();
  BoolColumn get isArchived => boolean().withDefault(const Constant(false))();
  TextColumn get color => text().withDefault(const Constant(''))();
  DateTimeColumn get createdAt => dateTime()();
  DateTimeColumn get updatedAt => dateTime()();
  DateTimeColumn get deletedAt => dateTime().nullable()();

  IntColumn  get isDirty => integer().withDefault(const Constant(0))();
  IntColumn  get baseRev => integer().withDefault(const Constant(0))();
  DateTimeColumn get localUpdatedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};
}
```

## Handling `deleted_at`

The server keeps tombstones forever, so the pull must apply them:

```dart
if (row['deleted_at'] != null) {
  await (db.delete(db.gymLocations)..where((t) => t.id.equals(id))).go();
} else {
  await db.into(db.gymLocations).insertOnConflictUpdate(row);
}
```

Housekeeping (purging old tombstones) is a server-side concern; the client just
honours them.