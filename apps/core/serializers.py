from __future__ import annotations

from rest_framework import serializers


class OwnerScopedRelationsMixin:
    """Restrict every related field to rows the requesting athlete owns.

    ``ModelSerializer`` builds ``PrimaryKeyRelatedField`` with
    ``queryset=Model.objects.all()``. Combined with ``perform_create`` forcing
    ``user=request.user``, that leaves a hole: the *new* row is correctly owned,
    but the attacker chooses which existing row to hang it off. That leaks names
    through ``exercise_name``/``location_name`` and, worse, lets one athlete
    inject sets into another athlete's workout.

    Narrowing the queryset to ``user=request.user`` closes it at the
    deserialisation boundary, so a foreign id fails validation with a 400
    instead of being silently accepted.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        user = getattr(self.context.get("request"), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return
        for field in self.fields.values():
            if isinstance(field, serializers.PrimaryKeyRelatedField):
                queryset = field.get_queryset()
                if queryset is not None and hasattr(queryset, "filter"):
                    field.queryset = queryset.filter(user=user)

    @property
    def request_user(self):
        return getattr(self.context.get("request"), "user", None)


class OwnerScopedRelatedField(serializers.PrimaryKeyRelatedField):
    """For models whose ``Meta.fields`` is assembled dynamically."""

    def __init__(self, *args, user_field: str = "user", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.user_field = user_field

    def get_queryset(self):
        queryset = super().get_queryset()
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return queryset.none()
        return queryset.filter(**{self.user_field: user})