"""Base models implementing the offline-first sync protocol.

Design
------
Every user-owned record is a :class:`SyncableModel`:

* ``id`` is a **client-generated UUID**. A phone with no signal can create a
  workout, go to sleep, and sync hours later without coordination.
* ``rev`` is bumped on every server-side write. Clients send the ``rev`` they
  last observed as ``base_rev``; a mismatch means someone (usually the same
  user on another device) changed the row in the meantime.
* ``updated_at`` / ``deleted_at`` implement soft delete so a delete on one
  device propagates to the others instead of the row silently reappearing.

Ordering for delta pulls comes from :class:`SyncState`, a per-user monotonic
counter. Pulling ``?since=N`` returns everything with ``seq > N`` in one pass,
which avoids the "rows changed while you were paging" problem entirely.
"""

from __future__ import annotations

import uuid

from django.db import models, transaction
from django.utils import timezone


class SyncState(models.Model):
    """Monotonic per-user sequence used as the delta-pull cursor."""

    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="sync_state"
    )
    seq = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "sync state"
        verbose_name_plural = "sync states"

    def __str__(self) -> str:
        return f"sync_state(user={self.user_id}, seq={self.seq})"


def next_sequence(user_id: int) -> int:
    """Atomically allocate the next sequence number for ``user_id``.

    Uses a single ``UPDATE ... RETURNING`` so concurrent writes from different
    devices can never be handed the same number.
    """
    with transaction.atomic():
        row = (
            SyncState.objects.filter(user_id=user_id)
            .update(seq=models.F("seq") + 1)
        )
        if not row:
            SyncState.objects.create(user_id=user_id, seq=1)
        return SyncState.objects.values_list("seq", flat=True).get(user_id=user_id)


class SyncableModel(models.Model):
    """Abstract base for every record that participates in sync."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: Starts at 0 so the first save yields ``rev == 1``.
    rev = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)

    #: Sequence assigned at write time. Indexed for fast delta pulls.
    seq = models.BigIntegerField(default=0, db_index=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs) -> None:
        """Stamp every write so nothing can reach the database unsynced.

        Living here rather than in a service layer means rows created through
        the plain REST endpoints, the admin, or a management command are picked
        up by the next delta pull exactly like rows created by ``/sync/push/``.
        """
        self.updated_at = timezone.now()
        self.rev = (self.rev or 0) + 1
        self.seq = self.allocate_seq()

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"updated_at", "rev", "seq"}

        super().save(*args, **kwargs)

    def allocate_seq(self) -> int:
        self.seq = next_sequence(self.user_id)  # type: ignore[attr-defined]
        return self.seq

    def soft_delete(self, user_id: int = None) -> None:
        """Mark the row deleted without removing it, so peers see the tombstone."""
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def restore(self, user_id: int = None) -> None:
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None