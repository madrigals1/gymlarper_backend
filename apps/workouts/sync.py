"""The offline-first sync protocol.

Push  ``POST /api/v1/sync/push/``  -> apply a batch of local edits
Pull  ``GET  /api/v1/sync/pull/``  -> everything that changed since a cursor

Guarantees
----------
* **Idempotent.** Every record has a client-generated UUID, so replaying a batch
  (a flaky connection retrying a POST) is harmless: creates become no-ops and
  updates are skipped when the revision is stale.
* **Ordered.** ``SyncState.seq`` is allocated atomically per user, so a client can
  pull "everything after N" in a single pass with no missed or duplicated rows.
* **Deletions propagate.** Deletes are tombstones (``deleted_at``), so removing a
  set on one device removes it on the others instead of resurrecting it.
* **Conflict safe.** Clients send the ``base_rev`` they last saw. A mismatch means
  another device won the race, and the server returns its version untouched
  rather than silently clobbering it.
* **Tenant safe.** Every op is forced into ``request.user``; a client cannot write
  to, or read, another athlete's rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from rest_framework.exceptions import ValidationError

from . import units as unit_utils
from .models import (
    Exercise,
    GymLocation,
    MachineSetup,
    TemplateItem,
    Workout,
    WorkoutExercise,
    WorkoutSet,
    WorkoutTemplate,
)
from .serializers import (
    ExerciseSerializer,
    GymLocationSerializer,
    MachineSetupSerializer,
    TemplateItemSerializer,
    WorkoutExerciseSerializer,
    WorkoutSerializer,
    WorkoutSetSerializer,
    WorkoutTemplateSerializer,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncType:
    name: str
    model: Any
    serializer: Any
    #: Fields the client is never allowed to set.
    immutable: tuple[str, ...] = ()


SYNC_TYPES: dict[str, SyncType] = {
    "location": SyncType("location", GymLocation, GymLocationSerializer, immutable=("user",)),
    "exercise": SyncType("exercise", Exercise, ExerciseSerializer, immutable=("user",)),
    "machine_setup": SyncType(
        "machine_setup", MachineSetup, MachineSetupSerializer, immutable=("user",)
    ),
    "template": SyncType(
        "template", WorkoutTemplate, WorkoutTemplateSerializer, immutable=("user",)
    ),
    "template_item": SyncType(
        "template_item", TemplateItem, TemplateItemSerializer, immutable=("user",)
    ),
    "workout": SyncType("workout", Workout, WorkoutSerializer, immutable=("user",)),
    "workout_exercise": SyncType(
        "workout_exercise", WorkoutExercise, WorkoutExerciseSerializer, immutable=("user",)
    ),
    "workout_set": SyncType("workout_set", WorkoutSet, WorkoutSetSerializer, immutable=("user",)),
}

#: Children whose parent must exist and belong to the same athlete. Guards
#: against a client attaching a set to somebody else's workout.
PARENT_LINKS: dict[str, tuple[str, str]] = {
    "machine_setup": ("exercise", "exercise"),
    "template_item": ("template", "template"),
    "workout_exercise": ("workout", "workout"),
    "workout_set": ("workout_exercise", "workout_exercise"),
}

#: Fields that must belong to the requesting user, not merely exist.
FOREIGN_USER_SCOPED: dict[str, tuple[str, ...]] = {
    "machine_setup": ("exercise", "location"),
    "template_item": ("template", "exercise"),
    "workout": ("template", "location"),
    "workout_exercise": ("workout", "exercise"),
    "workout_set": ("workout_exercise",),
}

#: Denormalised snapshot columns, kept consistent with their source of truth.
#: ``source field -> {local attribute: attribute on the related object}``
SNAPSHOT_SOURCES: dict[str, dict[str, dict[str, str]]] = {
    "workout_exercise": {
        "exercise": {
            "exercise_name": "name",
            "muscle_group": "muscle_group",
            "equipment": "equipment",
        },
    },
}

MAX_PUSH_OPS = 500
MAX_PULL_PER_TYPE = 500


def _strip_immutable(sync_type: SyncType, data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in sync_type.immutable}


def _validate_ownership(sync_type: SyncType, data: dict, user) -> None:
    """Every foreign key must point at a row the caller already owns."""
    for field in FOREIGN_USER_SCOPED.get(sync_type.name, ()):
        related_id = data.get(field)
        if not related_id:
            continue
        related_model = _related_model(field)
        if related_model is None:
            continue
        if not related_model.objects.filter(pk=related_id, user=user).exists():
            raise ValidationError({field: "Referenced record does not exist."})


def _related_model(field: str):
    return {
        "exercise": Exercise,
        "location": GymLocation,
        "template": WorkoutTemplate,
        "workout": Workout,
        "workout_exercise": WorkoutExercise,
    }.get(field)


def _apply_snapshots(sync_type_name: str, instance, data: dict) -> bool:
    """Keep denormalised columns in step with their source of truth.

    Returns True when something changed, so the caller only pays for an extra
    write (and the revision bump that comes with it) when it has to.
    """
    changed = False
    for source_field, mapping in SNAPSHOT_SOURCES.get(sync_type_name, {}).items():
        related_id = data.get(source_field)
        if not related_id:
            continue
        related_model = _related_model(source_field)
        related = related_model.objects.filter(pk=related_id).first() if related_model else None
        if related is None:
            continue
        for target_attr, source_attr in mapping.items():
            value = getattr(related, source_attr, "") or ""
            if getattr(instance, target_attr, None) != value:
                setattr(instance, target_attr, value)
                changed = True
    return changed


@transaction.atomic
def apply_push(user, ops: list[dict]) -> list[dict]:
    """Apply a batch of client operations and report the outcome of each."""
    results: list[dict] = []

    for op in ops:
        type_name = op.get("type")
        op_id = op.get("id")
        action = op.get("action", "upsert")
        base_rev = op.get("base_rev") or 0

        sync_type = SYNC_TYPES.get(type_name)
        if sync_type is None:
            results.append(_reject(op_id, type_name, f"Unknown sync type '{type_name}'."))
            continue
        if not op_id:
            results.append(_reject(op_id, type_name, "Every operation needs an id."))
            continue

        # A nested atomic block becomes a SAVEPOINT, so one malformed op rolls
        # back only itself instead of poisoning the whole batch.
        try:
            with transaction.atomic():
                results.append(
                    _apply_one(user, sync_type, op_id, action, base_rev, op.get("data") or {})
                )
        except ValidationError as exc:
            detail = exc.detail
            results.append(
                _reject(op_id, type_name, str(detail if isinstance(detail, str) else detail))
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Sync op %s %s failed", type_name, op_id)
            results.append(_reject(op_id, type_name, f"{type_name}: {exc}"))

    return results


def _apply_one(user, sync_type: SyncType, op_id, action: str, base_rev: int, data: dict) -> dict:
    existing = sync_type.model.objects.filter(pk=op_id, user=user).first()

    if action == "delete":
        if existing is None:
            # Already gone (or never existed). Deleting is idempotent by design.
            return _ok(op_id, sync_type.name, "applied", existing)
        if existing.rev != base_rev:
            return _conflict(existing, sync_type)
        if existing.deleted_at is None:
            existing.soft_delete(user.id)
        return _ok(op_id, sync_type.name, "applied", existing)

    if existing is None:
        payload = _strip_immutable(sync_type, data)
        _validate_ownership(sync_type, payload, user)
        serializer = sync_type.serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        # Pass the id explicitly: it is read-only on the serializer, but the
        # whole offline-create story depends on honouring the client's UUID.
        instance = serializer.save(user=user, id=op_id)
        if _apply_snapshots(sync_type.name, instance, payload):
            instance.save()
        return _ok(op_id, sync_type.name, "applied", instance)

    if existing.rev != base_rev:
        # Someone else (usually the same user on a phone) got there first.
        return _conflict(existing, sync_type)

    if existing.deleted_at is not None and action != "undelete":
        return _conflict(existing, sync_type)

    payload = _strip_immutable(sync_type, data)
    _validate_ownership(sync_type, payload, user)
    serializer = sync_type.serializer(existing, data=payload, partial=True)
    serializer.is_valid(raise_exception=True)
    instance = serializer.save()
    if _apply_snapshots(sync_type.name, instance, payload):
        instance.save()
    return _ok(op_id, sync_type.name, "applied", instance)


def _serialize(sync_type: SyncType, instance) -> dict:
    if instance is None:
        return {}
    return sync_type.serializer(instance).data


def _ok(op_id, type_name: str, status: str, instance) -> dict:
    return {
        "id": str(op_id),
        "type": type_name,
        "status": status,
        "rev": getattr(instance, "rev", 0),
        "data": _serialize(SYNC_TYPES[type_name], instance) if instance else None,
    }


def _conflict(instance, sync_type: SyncType) -> dict:
    """Report the winning server row so the client can merge rather than guess."""
    return {
        "id": str(instance.pk),
        "type": sync_type.name,
        "status": "conflict",
        "rev": instance.rev,
        "server_rev": instance.rev,
        "data": sync_type.serializer(instance).data,
    }


def _reject(op_id, type_name, message: str, *, status: str = "rejected") -> dict:
    return {
        "id": str(op_id) if op_id else None,
        "type": type_name,
        "status": status,
        "error": message,
    }


def build_pull(user, since: int, limit: int = MAX_PULL_PER_TYPE) -> dict:
    """Collect every row the caller has not seen yet.

    The cursor is the watermark the client resumes from, so it must never pass a
    row we did not actually send. When a type is truncated the safe watermark is
    the *lowest* point any type reached - a global maximum would skip the
    remainder of the truncated type forever.
    """
    limit = max(1, min(limit, MAX_PULL_PER_TYPE))
    changes: dict[str, list[dict]] = {}
    delivered_max = since
    truncated_watermarks: list[int] = []

    for type_name, sync_type in SYNC_TYPES.items():
        rows = list(
            sync_type.model.objects.filter(user=user, seq__gt=since).order_by("seq")[: limit + 1]
        )
        if len(rows) > limit:
            rows = rows[:limit]
            truncated_watermarks.append(rows[-1].seq)
        if rows:
            changes[type_name] = [sync_type.serializer(row).data for row in rows]
            delivered_max = max(delivered_max, max(row.seq for row in rows))

    has_more = bool(truncated_watermarks)
    cursor = min(truncated_watermarks) if has_more else delivered_max

    return {"cursor": cursor, "has_more": has_more, "changes": changes}


def unit_for_session(unit: str | None) -> str:
    return unit_utils.normalise_unit(unit)
